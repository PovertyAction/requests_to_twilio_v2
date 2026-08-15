"""Launch Twilio Studio flow executions for a list of phone numbers.

The output file this writes is a delivery tracker: one row per number, carrying
the execution SID and status. It is written incrementally, so a run that dies
halfway - a dropped connection, a closed laptop - leaves a complete record of
everything sent up to that point, and ``--resume`` can pick up from there. The
pre-2.0 launcher held all results in memory and wrote the spreadsheet only after
the final send, so any interruption lost the record of messages that had already
gone out, with no way to tell which respondents had been contacted.
"""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .log import get_logger, mask_phone

#: The one column an input file must have.
NUMBER_COLUMN = "Number"

#: The respondent's study identifier in the master list. Everything this project
#: writes outside the master list is keyed on it, so it is required rather than
#: optional: without it a round cannot be tracked without recording numbers.
CASEID_COLUMN = "caseid"

#: Execution parameter carrying the send time in UTC. Supplied by the launcher
#: rather than read from the sample file, so it is always available to the flow
#: as ``{{flow.data.sent_at}}`` without anyone having to remember a column.
SENT_AT_PARAM = "sent_at"

#: Above this many recipients, per-respondent success lines drop to DEBUG and
#: progress is reported per batch instead. A round of several hundred otherwise
#: buries its own failures in a wall of successes.
_VERBOSE_ROW_LIMIT = 25

#: Columns of the delivery tracker, in order.
#:
#: Keyed on `caseid`, never on the phone number. An unencrypted number lives in
#: exactly two places in this project - the master list the round is drawn from,
#: and the dataset after `rtt decrypt` - and the tracker is neither. It used to
#: carry the number twice, as `number` and again as `contact` (Twilio returns
#: the channel address on the execution), which put a Confidential identifier in
#: a file that gets copied around, mailed, and pasted into tickets.
#:
#: Nothing is lost: `caseid` joins to the master list on one side and to the
#: published row on the other, so the number is always one join away for
#: whoever is entitled to it.
TRACKER_COLUMNS = [
    "caseid",
    "status",
    "execution_sid",
    "url",
    "error",
    "sent_at",
]

#: Statuses that mean the message left successfully and must not be re-sent.
_SENT_STATUSES = frozenset({"active", "ended"})

logger = get_logger()


class LaunchError(Exception):
    """Raised when a run cannot start, e.g. a malformed input file."""


@dataclass
class DeliveryRecord:
    """One row of the delivery tracker, identified by caseid rather than number."""

    caseid: str
    status: str = ""
    execution_sid: str = ""
    url: str = ""
    error: str = ""
    sent_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_row(self) -> dict[str, Any]:
        """Return the record as a dict ordered like :data:`TRACKER_COLUMNS`."""
        data = asdict(self)
        return {column: data[column] for column in TRACKER_COLUMNS}


def is_retryable(exc: BaseException) -> bool:
    """Decide whether a Twilio failure is worth retrying.

    Rate limits and server-side faults are transient. A 400 for a malformed
    number, or a 401 for bad credentials, will fail identically every time, so
    retrying only delays the error the operator needs to see.

    Public because :mod:`requests_to_twilio.fetch` needs the same judgement: it
    makes one API call per execution, which is the other place in this package
    that generates enough traffic to be throttled.
    """
    if isinstance(exc, TwilioRestException):
        # `status` is None when the request never got a response. Comparing that
        # to 500 raises TypeError, which would replace the failure being handled
        # with a confusing one from inside the handler.
        status = exc.status
        if status is None:
            return True
        return status == 429 or status >= 500
    # Connection-level failures surface as plain OSErrors.
    return isinstance(exc, OSError)


@retry(
    retry=retry_if_exception(is_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _create_execution(
    client: Client,
    flow_id: str,
    to_number: str,
    from_number: str,
    parameters: dict[str, str],
) -> Any:
    """Create one Studio execution, retrying transient failures."""
    return client.studio.v2.flows(flow_id).executions.create(
        to=to_number, from_=from_number, parameters=parameters
    )


def read_input(input_file: Path, columns_to_send: list[str]) -> pd.DataFrame:
    """Read and validate the sample file.

    Args:
        input_file: An ``.xlsx`` or ``.csv`` file with a ``Number`` column.
        columns_to_send: Extra columns to pass to the flow as parameters.

    Returns:
        The loaded frame.

    Raises:
        LaunchError: If the file is unreadable, lacks ``Number``, or lacks one
            of the requested columns. Validating up front matters: discovering a
            typo in a column name after 300 messages have gone out is expensive
            and irreversible.

    """
    if not input_file.is_file():
        raise LaunchError(f"Input file not found: {input_file}")

    suffix = input_file.suffix.lower()
    try:
        if suffix in {".xlsx", ".xlsm"}:
            frame = pd.read_excel(input_file, dtype=str)
        elif suffix == ".csv":
            frame = pd.read_csv(input_file, dtype=str)
        else:
            raise LaunchError(
                f"Unsupported input format {suffix!r}; use .xlsx or .csv."
            )
    except LaunchError:
        raise
    except Exception as exc:
        raise LaunchError(f"Could not read {input_file}: {exc}") from exc

    if NUMBER_COLUMN not in frame.columns:
        raise LaunchError(
            f"Input file has no {NUMBER_COLUMN!r} column. "
            f"Found: {', '.join(map(str, frame.columns))}"
        )

    # Required, not optional. The tracker, the delivery log and both sheet tabs
    # are keyed on caseid precisely so none of them has to hold a phone number;
    # without one there is nothing to key them on, and the only way to follow a
    # round would be to write numbers into all four.
    if CASEID_COLUMN not in frame.columns:
        raise LaunchError(
            f"Input file has no {CASEID_COLUMN!r} column. Every respondent needs "
            f"one: it is what the delivery tracker, the delivery log and the "
            f"published row are keyed on, so that none of them has to store a "
            f"phone number.\nFound: {', '.join(map(str, frame.columns))}"
        )

    blank = frame[CASEID_COLUMN].isna() | (
        frame[CASEID_COLUMN].astype(str).str.strip() == ""
    )
    if blank.any():
        rows = ", ".join(str(i + 2) for i in frame.index[blank][:5])
        raise LaunchError(
            f"{int(blank.sum())} row(s) have a blank {CASEID_COLUMN!r} "
            f"(spreadsheet row {rows}). A blank one cannot identify a "
            f"respondent, so their delivery status would be unattributable."
        )

    duplicated = frame[CASEID_COLUMN].astype(str).str.strip().duplicated()
    if duplicated.any():
        repeats = ", ".join(
            sorted(set(frame.loc[duplicated, CASEID_COLUMN].astype(str)))[:5]
        )
        raise LaunchError(
            f"{CASEID_COLUMN!r} repeats ({repeats}). It is the key every other "
            f"file joins on, so a duplicate silently merges two respondents' "
            f"delivery status into one row."
        )

    missing = [c for c in columns_to_send if c not in frame.columns]
    if missing:
        raise LaunchError(
            f"Requested column(s) not in input file: {', '.join(missing)}. "
            f"Available: {', '.join(map(str, frame.columns))}"
        )

    frame = frame[frame[NUMBER_COLUMN].notna()]
    if frame.empty:
        raise LaunchError(f"No usable rows: every {NUMBER_COLUMN} value is blank.")

    return frame


def tracker_path(input_file: Path) -> Path:
    """Return the delivery tracker path for a given input file."""
    return input_file.with_name(f"{input_file.stem}_output.csv")


def already_sent(tracker: Path) -> set[str]:
    """Read a tracker and return the caseids that were successfully sent.

    Args:
        tracker: Path to an existing tracker file. A missing file is not an
            error; it simply means nothing has been sent yet.

    Returns:
        The set of caseids to skip on a resumed run.

    """
    if not tracker.is_file():
        return set()

    sent: set[str] = set()
    with tracker.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row.get("status") or "").lower() in _SENT_STATUSES:
                sent.add((row.get(CASEID_COLUMN) or "").strip())
    return sent


class _TrackerWriter:
    """Appends delivery records to the tracker, flushing after every row."""

    def __init__(self, path: Path) -> None:
        self._path = path
        # Size, not existence: a run that died before writing its first record
        # leaves a zero-byte file behind. Treating that as "already has a
        # header" produces a tracker whose first data row is read back as the
        # header, so `already_sent` reports nothing sent and --resume re-sends
        # to the first respondent.
        self._existed = path.is_file() and path.stat().st_size > 0
        # Plain utf-8, deliberately not utf-8-sig like the CSVs meant for
        # Excel. The tracker is read back by `already_sent` with the stdlib
        # `csv` module, which does not strip a BOM: the first fieldname would
        # become "﻿caseid", `row.get("caseid")` would return None, and
        # --resume would report nothing sent and re-send to everyone.
        self._handle = path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._handle, fieldnames=TRACKER_COLUMNS)
        if not self._existed:
            self._writer.writeheader()
            self._handle.flush()

    def write(self, record: DeliveryRecord) -> None:
        """Append one record and flush it to disk immediately."""
        self._writer.writerow(record.as_row())
        # Flushing per row is what makes an interrupted run recoverable. The
        # cost is negligible next to the network call that precedes it.
        self._handle.flush()

    def close(self) -> None:
        """Close the underlying file."""
        self._handle.close()

    def __enter__(self) -> _TrackerWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def launch(
    *,
    client: Client,
    flow_id: str,
    from_number: str,
    input_file: Path,
    columns_to_send: list[str],
    batch_size: int,
    sec_between_batches: float,
    resume: bool = False,
    dry_run: bool = False,
) -> Path:
    """Send a Studio flow execution to every number in the sample file.

    Args:
        client: An authenticated Twilio client.
        flow_id: The Studio flow SID.
        from_number: The sending number, e.g. ``whatsapp:+14155238886``.
        input_file: The sample file.
        columns_to_send: Columns passed through to the flow as parameters.
        batch_size: How many messages to send before pausing.
        sec_between_batches: How long to pause between batches.
        resume: Skip numbers already recorded as sent in the tracker.
        dry_run: Validate and report without sending anything.

    Returns:
        The path to the delivery tracker.

    """
    frame = read_input(input_file, columns_to_send)
    tracker = tracker_path(input_file)

    # A tracker with content in it means this sample was launched before. The
    # writer opens in append mode, so a second run without --resume would send
    # to everyone again and record it in the same file with nothing marking
    # where run one ended - each respondent getting a second execution and a
    # second warehouse row. Refuse instead, and say which flag resolves it.
    #
    # Size, not just existence: a run that died before writing its first record
    # leaves a zero-byte file carrying no information, and refusing that would
    # block a launch that never happened.
    if tracker.is_file() and tracker.stat().st_size > 0 and not resume and not dry_run:
        previously_sent = already_sent(tracker)
        if previously_sent:
            detail = f"{len(previously_sent)} number(s) recorded as sent"
            remedy = "  --resume     send only to the numbers that have not gone out\n"
        else:
            # Every attempt failed - a bad sending number, the wrong flow SID,
            # an unapproved template. Offering --resume here would be actively
            # misleading, since it would retry the same broken configuration.
            detail = "every attempt in it failed"
            remedy = (
                "  Fix the cause of the failures first - the tracker's "
                "`error` column says what Twilio returned.\n"
            )
        raise LaunchError(
            f"{tracker.name} already exists: this sample has been launched "
            f"before ({detail}).\n\n"
            f"{remedy}"
            f"  --dry-run    show what would be sent, without sending\n\n"
            f"To start over, move or delete {tracker.name} first."
        )

    skip = already_sent(tracker) if resume else set()
    if resume:
        logger.info("Resuming: %d number(s) already sent will be skipped", len(skip))

    pending = [
        row
        for _, row in frame.iterrows()
        if str(row[CASEID_COLUMN]).strip() not in skip
    ]

    logger.info(
        "%d row(s) in %s, %d to send%s",
        len(frame),
        input_file.name,
        len(pending),
        " (dry run, nothing will be sent)" if dry_run else "",
    )

    if dry_run:
        for row in pending[:5]:
            logger.info(
                "would send to %s with parameters %s",
                mask_phone(str(row[NUMBER_COLUMN])),
                {c: "<value>" for c in columns_to_send}
                | {SENT_AT_PARAM: "<utc timestamp>"},
            )
        if len(pending) > 5:
            logger.info("... and %d more", len(pending) - 5)
        return tracker

    sent_in_batch = 0
    succeeded = 0
    failed = 0

    # One line per respondent is what you want while testing five numbers and
    # noise once there are five hundred. Below the threshold every send is
    # reported; above it, successes drop to DEBUG (still there under --verbose)
    # and progress is reported per batch instead. Failures are ERROR either way
    # - those are never noise.
    per_row_level = (
        logging.INFO if len(pending) <= _VERBOSE_ROW_LIMIT else logging.DEBUG
    )
    started = time.monotonic()

    with _TrackerWriter(tracker) as writer:
        for position, row in enumerate(pending, start=1):
            to_number = str(row[NUMBER_COLUMN]).strip()
            caseid = str(row[CASEID_COLUMN]).strip()
            record = DeliveryRecord(caseid=caseid)

            parameters = {c: str(row[c]) for c in columns_to_send}
            # The moment this respondent was contacted, in UTC, handed to the
            # flow so it can publish it alongside the answers. Studio cannot
            # produce a UTC timestamp itself - its Liquid date filter renders in
            # Twilio's own timezone and has no way to convert - so the only
            # clock the flow can trust is one supplied from outside.
            #
            # Deliberately the same value the tracker records, not a second
            # call to now(): the delivery tracker and the published row should
            # agree about when a message went out, to the character.
            parameters[SENT_AT_PARAM] = record.sent_at

            # Logged by caseid, with the masked number beside it. The console is
            # the one place the operator needs both: they are looking at a
            # failure and deciding whether the number in the master list is
            # wrong. Nothing written to disk carries either form.
            who = f"{caseid} ({mask_phone(to_number)})"

            try:
                execution = _create_execution(
                    client, flow_id, to_number, from_number, parameters
                )
            except TwilioRestException as exc:
                record.status = "failed"
                record.error = f"HTTP {exc.status} (code {exc.code}): {exc.msg}"
                failed += 1
                logger.error("%s -> %s", who, record.error)
            except Exception as exc:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
                failed += 1
                logger.error("%s -> %s", who, record.error)
            else:
                record.status = execution.status or "unknown"
                record.execution_sid = execution.sid or ""
                record.url = execution.url or ""
                succeeded += 1
                logger.log(
                    per_row_level,
                    "%s -> %s (%s)  [%d/%d]",
                    who,
                    record.status,
                    record.execution_sid,
                    position,
                    len(pending),
                )

            writer.write(record)

            sent_in_batch += 1
            if sent_in_batch == batch_size and position < len(pending):
                rate = position / max(time.monotonic() - started, 0.001)
                remaining = (len(pending) - position) / max(rate, 0.001)
                logger.info(
                    "%d/%d done (%d failed), %.1f/s, ~%.0fs left. "
                    "Pausing %.1fs between batches.",
                    position,
                    len(pending),
                    failed,
                    rate,
                    remaining,
                    sec_between_batches,
                )
                time.sleep(sec_between_batches)
                sent_in_batch = 0

    logger.info("Finished: %d sent, %d failed", succeeded, failed)
    logger.info("Delivery tracker: %s", tracker)
    if failed:
        logger.warning(
            "Re-run with --resume to retry only the %d failed number(s).", failed
        )

    return tracker
