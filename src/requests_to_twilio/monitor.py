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

from datetime import UTC, datetime
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

#: The key a row is updated by. A message SID is stable for the message's life,
#: which is what makes updating in place possible at all.
LOG_KEY = "message_sid"

#: Statuses that will not change again, so a row carrying one is final.
TERMINAL_STATUSES = frozenset({"delivered", "read", "failed", "undelivered"})

#: Statuses that mean an outbound message did not arrive.
FAILED_STATUSES = frozenset({"failed", "undelivered"})


class MonitorError(Exception):
    """Raised when delivery status cannot be retrieved."""


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


def update_log(frame: pd.DataFrame, path: Path) -> tuple[int, int]:
    """Merge a poll into the running file, updating rows already present.

    Args:
        frame: A poll's results, as returned by :func:`poll_delivery`.
        path: The CSV to create or update.

    Returns:
        How many rows were added, and how many existing rows changed state.

    A message already in the file is replaced rather than appended, so the file
    stays one row per message however often it is polled. "Changed state" counts
    only rows whose status actually moved - a poll that finds nothing new should
    say so rather than reporting every row as touched.

    """
    fresh = frame.copy()
    if fresh.empty:
        logger.info("Nothing to write: the poll returned no messages")
        return (0, 0)

    if path.is_file():
        existing = pd.read_csv(path, dtype=str).fillna("")
        previous = dict(zip(existing[LOG_KEY], existing["status"], strict=False))
    else:
        existing = pd.DataFrame(columns=LOG_COLUMNS)
        previous = {}

    added = sum(1 for sid in fresh[LOG_KEY] if sid not in previous)
    changed = sum(
        1
        for sid, status in zip(fresh[LOG_KEY], fresh["status"], strict=False)
        if sid in previous and previous[sid] != status
    )

    # Keep rows the poll's window did not cover, then let the poll win for any
    # message it did see: it is by definition the more recent reading.
    kept = existing[~existing[LOG_KEY].isin(set(fresh[LOG_KEY]))]
    merged = pd.concat([kept, fresh], ignore_index=True)
    merged = merged.sort_values("date_sent", na_position="first")

    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False)
    logger.info(
        "%s: %d message(s) total, %d new, %d changed state",
        path.name,
        len(merged),
        added,
        changed,
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
