"""Poll message delivery status during a round, into a file that keeps updating.

This reads the layer nothing else in this toolkit looks at. `rtt fetch` reads
Studio executions and `rtt data-check` reads the published table, and both are
blind to the same class of failure: a message that never became an execution.

    Messages API        delivery status, error codes    <- this module
    Studio executions   rtt fetch                       flow progress
    Warehouse rows      rtt data-check                  collected data

A send that Meta rejects, or that the API refuses, produces no execution and
therefore no published row. The respondent is not `incomplete` in the data - they
are *absent from it*, which is the one state a status column cannot express. The
only record is the message itself.

**One row per message, updated in place.** A message's status changes after it is
sent - queued, sent, delivered, read - so appending each poll would write the
same message repeatedly and leave the reader to work out which line is current.
Instead the file is keyed by message SID and rewritten, so it always shows the
present state of the round and does not grow with how often you look at it.
Twilio's own `date_updated` records when the status last changed, so the timing
of a transition survives without storing snapshots.

**A rate limit is not an empty round.** Polling repeatedly is what this is for,
and repeated polling is what earns a 429. Returning "no messages" when throttled
would report a healthy quiet round at exactly the moment the account is busiest,
which is the failure this module refuses to have - the same confusion between a
rate limit and an absence of data that once passed for Twilio's retention window.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .log import get_logger, mask_phone

logger = get_logger()

#: The file's columns, in reading order: who and what, then state, then timing.
LOG_COLUMNS = [
    "message_sid",
    "to",
    "from_",
    "direction",
    "status",
    "error_code",
    "error_message",
    "date_sent",
    "date_updated",
    "polled_at",
]

#: What the running log holds: one row per respondent, not per message.
#:
#: A round of 4 people produced 71 messages - consent, five questions, retries,
#: closings, and every reply. Nobody monitoring a round wants 71 rows; they want
#: to know which of the 4 got their message and who has gone quiet. The
#: per-message frame is still what the API returns and what the aggregation reads,
#: but it is not what gets written.
#:
#: Keyed on `caseid`, never the phone number. The Messages API can only answer
#: in phone numbers - `to` and `from_` are the only respondent identity it
#: returns - so the number is resolved to a caseid against the master list as
#: each poll is aggregated, and only the caseid is written. An unencrypted
#: number exists in exactly two places in this project: the master list, and the
#: dataset after `rtt decrypt`. The delivery log is neither, and it is the one
#: file most likely to be shared while a round is live.
NUMBER_COLUMNS = [
    "caseid",
    "delivery_status",
    "outbound",
    "inbound",
    "error_codes",
    "first_sent",
    "last_activity",
    "polled_at",
]

#: The key a row is updated by. A message SID is stable for the message's life,
#: which is what makes updating in place possible at all.
LOG_KEY = "message_sid"

#: The key the running log is updated by.
NUMBER_KEY = "caseid"

#: Prefix for a number the master list does not know. Somebody writing in
#: unprompted is worth seeing - it is how an unsolicited reply, or a number
#: typo'd into the sample, first shows up - but they have no caseid to be filed
#: under, and inventing a sequential one would change meaning between polls.
#:
#: The suffix is a truncated digest of the number: stable across polls, distinct
#: between senders, and not reversible to a phone number by anyone reading the
#: log or the sheet.
UNKNOWN_PREFIX = "unknown-"

#: Statuses that will not change again, so a row carrying one is final.
TERMINAL_STATUSES = frozenset({"delivered", "read", "failed", "undelivered"})

#: Statuses that mean an outbound message did not arrive.
FAILED_STATUSES = frozenset({"failed", "undelivered"})

#: Per-number states that will not change again, so polling them again learns
#: nothing. A `failed` opener does not un-fail, and once somebody has replied the
#: flow owns the conversation - their progress is a question for `rtt fetch` and
#: the published table, not for delivery status.
#:
#: This is also how the loop knows it is finished: when no number is still
#: pending there is nothing left to watch, and continuing to poll for the rest of
#: the requested window would only spend rate limit on a settled round.
SETTLED_STATES = frozenset({"failed", "answered_back", "unsolicited"})


#: How far before the first send to start looking. `sent_at` is stamped by the
#: launcher when the API accepted the request and `date_sent` by Twilio when it
#: went out, so the two can disagree by a moment in either direction. A minute
#: costs nothing and stops the first message of a round falling outside its own
#: window.
LAUNCH_MARGIN = timedelta(minutes=1)


class MonitorError(Exception):
    """Raised when delivery status cannot be retrieved."""


def read_tracker(path: Path) -> pd.DataFrame:
    """Read a delivery tracker written by ``rtt launch``.

    Args:
        path: The tracker CSV, named after the sample file.

    Returns:
        Its rows, as strings.

    Raises:
        MonitorError: If the file cannot be read or is not a tracker.

    """
    try:
        frame = pd.read_csv(path, dtype=str).fillna("")
    except OSError as exc:
        raise MonitorError(f"Could not read {path}: {exc}") from exc

    missing = {"status", "sent_at"} - set(frame.columns)
    if missing:
        raise MonitorError(
            f"{path} does not look like a delivery tracker - no "
            f"{', '.join(sorted(missing))} column. `rtt launch` writes one "
            "named after the sample file."
        )
    return frame


def launch_window(tracker: pd.DataFrame) -> datetime | None:
    """Return when the round started, from the earliest send.

    Scoping a poll to the launch rather than to a calendar day is the difference
    between reading this round and reading everything the account did that day.
    On the first live round of this instrument, `--since` at day resolution
    returned 91 messages for a round of 4.
    """
    stamps = pd.to_datetime(
        tracker["sent_at"], format="ISO8601", errors="coerce", utc=True
    )
    stamps = stamps.dropna()
    if stamps.empty:
        return None
    return stamps.min().to_pydatetime() - LAUNCH_MARGIN


def launch_failures(tracker: pd.DataFrame) -> pd.DataFrame:
    """Return the sends that never happened, identified by caseid.

    These are the rows no other view can show. The API refused the request or
    Meta rejected it, so no execution was created, no message exists to have a
    delivery status, and no row will ever be published. The respondent is absent
    from the dataset rather than incomplete in it, and the tracker is the only
    place they appear at all.

    No masking is needed any more: the tracker is keyed on caseid and holds no
    phone number to mask.
    """
    if tracker.empty:
        return tracker
    return tracker[tracker["status"].str.strip() == "failed"].copy()


def poll_delivery(
    *,
    client: Client,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Read message delivery status from the Messages API.

    Args:
        client: An authenticated Twilio client.
        since: Only messages sent at or after this time.
        until: Only messages sent at or before this time.
        limit: Stop after this many messages.

    Returns:
        One row per message, with :data:`LOG_COLUMNS`.

    Raises:
        MonitorError: If Twilio rejects the request - including a rate limit,
            which is reported as the error it is rather than as no data.

    """
    kwargs: dict[str, Any] = {}
    if since is not None:
        kwargs["date_sent_after"] = since
    if until is not None:
        kwargs["date_sent_before"] = until
    if limit is not None:
        kwargs["limit"] = limit

    try:
        # .list() walks pagination internally.
        messages = client.messages.list(**kwargs)
    except TwilioRestException as exc:
        if exc.status == 429:
            raise MonitorError(
                "Twilio rate-limited this poll (HTTP 429). This is a throttle, "
                "not an empty round - the messages are still there. Poll less "
                "often or narrow --since."
            ) from exc
        raise MonitorError(
            f"Could not list messages: HTTP {exc.status} (code {exc.code}): {exc.msg}"
        ) from exc

    polled_at = datetime.now(UTC).isoformat()
    rows = [
        {
            "message_sid": m.sid or "",
            "to": str(m.to or ""),
            "from_": str(m.from_ or ""),
            "direction": m.direction or "",
            "status": m.status or "",
            # Kept as given. An error code is the join key into Twilio's own
            # documentation, and reformatting it breaks that lookup.
            "error_code": "" if m.error_code is None else str(m.error_code),
            "error_message": m.error_message or "",
            "date_sent": m.date_sent.isoformat() if m.date_sent else "",
            "date_updated": m.date_updated.isoformat() if m.date_updated else "",
            "polled_at": polled_at,
        }
        for m in messages
    ]

    logger.info("Polled %d message(s)", len(rows))
    return pd.DataFrame(rows, columns=LOG_COLUMNS)


def read_master_list(path: Path) -> dict[str, str]:
    """Map every phone number in the master list to its caseid.

    Args:
        path: The sample file a round was launched from.

    Returns:
        Normalised number to caseid. Normalised means digits only, because the
        same respondent appears as ``+57 300 123 4567`` in a spreadsheet and
        ``whatsapp:+573001234567`` in the Messages API, and a literal comparison
        matches neither to the other.

    Raises:
        MonitorError: If the file cannot be read or lacks the two columns.

    This is the only file `rtt monitor` reads a phone number from, and the
    mapping stays in memory - nothing it writes carries a number.

    """
    try:
        if path.suffix.lower() in {".xlsx", ".xlsm"}:
            frame = pd.read_excel(path, dtype=str)
        else:
            frame = pd.read_csv(path, dtype=str)
    except OSError as exc:
        raise MonitorError(f"Could not read {path}: {exc}") from exc
    except Exception as exc:
        raise MonitorError(f"Could not read {path}: {exc}") from exc

    frame = frame.fillna("")
    missing = {"Number", "caseid"} - set(frame.columns)
    if missing:
        raise MonitorError(
            f"{path} is not a master list - no {', '.join(sorted(missing))} "
            f"column. It should be the same sample file the round was launched "
            f"from; it is what turns a phone number back into a caseid."
        )

    return {
        digits: str(row["caseid"]).strip()
        for _, row in frame.iterrows()
        if (digits := normalise_number(str(row["Number"])))
    }


def normalise_number(value: str) -> str:
    """Reduce a phone number to digits, so the two spellings of it compare.

    A master list holds ``+57 300 123 4567``; the Messages API returns
    ``whatsapp:+573001234567``. Neither leading ``+``, nor spaces, nor the
    channel prefix carry meaning for identity.
    """
    return "".join(character for character in str(value) if character.isdigit())


def _unknown_caseid(number: str) -> str:
    """Return a stable, non-reversible stand-in for a number we cannot name."""
    digest = hashlib.sha256(normalise_number(number).encode()).hexdigest()[:8]
    return f"{UNKNOWN_PREFIX}{digest}"


def by_number(
    frame: pd.DataFrame, caseids: dict[str, str] | None = None
) -> pd.DataFrame:
    """Collapse a poll to one row per respondent, as a state.

    Args:
        frame: A poll's results, as returned by :func:`poll_delivery`.
        caseids: Normalised number to caseid, from :func:`read_master_list`. A
            number that is not in it gets an ``unknown-`` stand-in rather than
            being dropped: somebody writing in unprompted is exactly what a live
            round wants to notice.

    Returns:
        One row per respondent, with :data:`NUMBER_COLUMNS`.

    The question a live round asks is not "what happened to each of these 71
    messages". It is: **we launched - did it land, and did they answer?** So each
    number gets one of four states, in order of progress:

        failed          the opener did not go out, or came back undelivered
        sent            accepted by Twilio, not yet confirmed on the handset
        delivered       it arrived (or was read)
        answered_back   they replied at least once

    ``answered_back`` outranks everything: a reply proves delivery more firmly
    than a delivery receipt does, and it is the only state that means the survey
    is actually running for that person.

    Only the **first** outbound message is judged. Later ones exist solely
    because the respondent was already engaging, so folding them in would let a
    mid-survey hiccup overwrite the fact that the round reached them.

    The respondent is whichever party is not the sending address - ``to`` when
    outbound, ``from_`` when inbound. Grouping on the raw ``to`` would file every
    reply under the Twilio number and report the round as one busy respondent.

    """
    if frame.empty:
        return pd.DataFrame(columns=NUMBER_COLUMNS)

    lookup = caseids or {}
    working = frame.copy()
    outbound = working["direction"].str.startswith("outbound")
    working["number"] = working["to"].where(outbound, working["from_"])
    working["_out"] = outbound

    rows = []
    for number, group in working.groupby("number", dropna=False):
        sends = group[group["_out"]].sort_values("date_sent")
        replies = group[~group["_out"]]
        opener = sends.iloc[0] if not sends.empty else None

        # "Did we ever message them" comes before "did they answer": with no
        # outbound at all there is nothing to have answered *to*. Testing the
        # reply first reported somebody who wrote in unprompted as a respondent
        # who answered a question that was never asked.
        if opener is None:
            state = "unsolicited"
        elif not replies.empty:
            state = "answered_back"
        elif opener["status"] in FAILED_STATUSES:
            state = "failed"
        elif opener["status"] in {"delivered", "read"}:
            state = "delivered"
        else:
            state = "sent"

        codes = sorted(
            {c for c in group["error_code"].fillna("").astype(str) if c.strip()}
        )
        rows.append(
            {
                "caseid": lookup.get(normalise_number(str(number)))
                or _unknown_caseid(str(number)),
                "delivery_status": state,
                "outbound": int(group["_out"].sum()),
                "inbound": int(len(replies)),
                "error_codes": ",".join(codes),
                "first_sent": opener["date_sent"] if opener is not None else "",
                "last_activity": group["date_updated"].max(),
                "polled_at": group["polled_at"].max(),
            }
        )

    return pd.DataFrame(rows, columns=NUMBER_COLUMNS).sort_values("first_sent")


def pending(log: pd.DataFrame) -> pd.DataFrame:
    """Return the numbers still worth polling.

    Everything not in :data:`SETTLED_STATES` - so `sent` waiting to become
    `delivered`, and `delivered` waiting for a reply that may never come.
    """
    if log.empty:
        return log
    return log[~log["delivery_status"].isin(SETTLED_STATES)]


def update_log(states: pd.DataFrame, path: Path) -> tuple[int, int]:
    """Merge a poll's per-number states into the running file.

    Args:
        states: One row per number, as returned by :func:`by_number`.
        path: The CSV to create or update.

    Returns:
        How many numbers were added, and how many changed state.

    One row per number however often it is polled. "Changed state" counts only
    numbers whose state actually moved - a quiet poll should say so rather than
    report every row as touched.

    **A settled state is never overwritten.** Once a number has failed or
    answered back it is finished, and a later poll with a narrower window might
    not see the message that settled it - which would quietly walk the row back
    to `sent` and put it back in the pending set forever.

    """
    fresh = states.copy()
    if fresh.empty:
        logger.info("Nothing to write: the poll returned no messages")
        return (0, 0)

    if path.is_file():
        existing = pd.read_csv(path, dtype=str).fillna("")
        previous = dict(
            zip(existing[NUMBER_KEY], existing["delivery_status"], strict=False)
        )
    else:
        existing = pd.DataFrame(columns=NUMBER_COLUMNS)
        previous = {}

    settled = {n for n, s in previous.items() if s in SETTLED_STATES}
    # Drop rows the log has already settled: this poll cannot know better.
    fresh = fresh[~fresh[NUMBER_KEY].isin(settled)]
    if fresh.empty:
        logger.info("Nothing new: every number in this window has settled")
        return (0, 0)

    added = sum(1 for n in fresh[NUMBER_KEY] if n not in previous)
    changed = sum(
        1
        for number, state in zip(
            fresh[NUMBER_KEY], fresh["delivery_status"], strict=False
        )
        if number in previous and previous[number] != state
    )

    kept = existing[~existing[NUMBER_KEY].isin(set(fresh[NUMBER_KEY]))]
    merged = pd.concat([kept, fresh], ignore_index=True)
    merged = merged.sort_values("first_sent", na_position="first")

    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False)
    logger.info(
        "%s: %d number(s), %d new, %d changed state, %d still pending",
        path.name,
        len(merged),
        added,
        changed,
        len(pending(merged)),
    )
    return (added, changed)


def summarise(frame: pd.DataFrame) -> pd.DataFrame:
    """Return counts by status and error code, worst first.

    The table a person actually reads during a round. Ordered so that anything
    that did not arrive is at the top, because that is the only part anyone can
    still act on while the round is running.
    """
    if frame.empty:
        return pd.DataFrame(columns=["status", "error_code", "messages"])

    grouped = (
        frame.groupby(["status", "error_code"], dropna=False)
        .size()
        .reset_index(name="messages")
    )
    grouped["_failed"] = grouped["status"].isin(FAILED_STATUSES) | (
        grouped["error_code"].fillna("").astype(str).str.strip() != ""
    )
    return (
        grouped.sort_values(["_failed", "messages"], ascending=[False, False])
        .drop(columns="_failed")
        .reset_index(drop=True)
    )


def problems(frame: pd.DataFrame) -> pd.DataFrame:
    """Return every message that failed, in either direction.

    Two different failures, and reporting only the first was this function's own
    bug on the day it was written:

    * **Outbound didn't arrive** - status ``failed`` or ``undelivered``. The
      respondent never saw the question.
    * **Inbound carries an error code** - status ``received``, which reads as
      success, with an error against it. The respondent replied and *Twilio could
      not hand it over*: error 11200 is a webhook that returned non-2xx, so the
      answer was accepted and dropped.

    The second is the more dangerous one, because every surface says it worked.
    The first live round of this instrument lost five replies that way while the
    delivery tracker showed four sends and zero failures - and the first version
    of this function reported "every message arrived" over the top of them.
    """
    if frame.empty:
        return frame
    failed = frame["status"].isin(FAILED_STATUSES)
    errored = frame["error_code"].fillna("").astype(str).str.strip() != ""
    found = frame[failed | errored].copy()
    if not found.empty:
        found["to"] = found["to"].map(lambda v: mask_phone(str(v)))
    return found
