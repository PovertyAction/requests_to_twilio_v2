"""Pull flow executions from the Twilio Studio API, for reconciliation.

The published table is the database of record, but the function that writes to
it can fail: a warehouse or Sheets error that outlives its retries drops that
respondent's row, and nothing downstream notices. This module fetches what
Twilio believes happened so the two can be compared, and so a dropped response
can be recovered.

Two things to know before using it:

* Twilio retains execution context for a limited window (30 days at the time of
  writing). Reconcile during collection, not months later.
* The context holds the answers **as the respondent sent them**, in plain text,
  along with the respondent's number and everything preloaded into
  ``flow.data``. The encryption in this project protects the copy that lands in
  the warehouse, which is the widely-shared surface; it was never able to
  protect the copy inside Twilio. Output from this command is therefore
  unencrypted PII, and this module is the standing proof that Twilio-side
  plaintext exists for the length of the retention window.

The pre-2.0 equivalent reconstructed answers by fuzzy string matching against
question text with a Jaccard similarity score, which is why the old README
called it unreliable. The Executions API returns the actual answer for each
widget, so no guessing is involved.
"""

from __future__ import annotations

from datetime import datetime
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

from .launcher import is_retryable
from .log import get_logger, mask_phone

logger = get_logger()

#: Execution-level fields captured for every row.
_BASE_COLUMNS = ["execution_sid", "contact", "status", "date_created", "date_updated"]

#: Added to a row whose answers could not be read. Its presence is what makes an
#: incomplete reconciliation legible instead of merely short.
CONTEXT_ERROR_COLUMN = "context_error"


class FetchError(Exception):
    """Raised when executions cannot be retrieved."""


@retry(
    retry=retry_if_exception(is_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _fetch_context(flow: Any, execution_sid: str) -> Any:
    """Fetch one execution's context, retrying transient failures.

    One request per execution, so a reconciliation over a few thousand rows is
    exactly the shape of traffic Twilio rate-limits. Without this the first 429
    silently costs an execution's answers.
    """
    return flow.executions(execution_sid).execution_context().fetch()


def _widget_answers(context: dict[str, Any]) -> dict[str, str]:
    """Flatten a Studio execution context into one answer per widget.

    Args:
        context: The ``context`` mapping from an execution context resource.

    Returns:
        A mapping of widget name to the body of the message the respondent sent
        to that widget. Widgets the respondent never reached are absent, which
        is what distinguishes a drop-off from a blank answer.

    """
    widgets = context.get("widgets") or {}
    answers: dict[str, str] = {}

    for name, payload in widgets.items():
        if not isinstance(payload, dict):
            continue
        inbound = payload.get("inbound")
        if isinstance(inbound, dict) and "Body" in inbound:
            answers[str(name)] = str(inbound["Body"])

    return answers


def fetch_executions(
    *,
    client: Client,
    flow_id: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int | None = None,
    include_answers: bool = True,
) -> pd.DataFrame:
    """Fetch executions of a Studio flow.

    Args:
        client: An authenticated Twilio client.
        flow_id: The Studio flow SID.
        date_from: Only executions created at or after this time.
        date_to: Only executions created at or before this time.
        limit: Stop after this many executions.
        include_answers: Also fetch each execution's context to recover the
            respondent's answers. This costs one extra API call per execution,
            so it can be turned off when only delivery status is needed.

    Returns:
        One row per execution.

    Raises:
        FetchError: If Twilio rejects the request.

    """
    flow = client.studio.v2.flows(flow_id)

    kwargs: dict[str, Any] = {}
    if date_from is not None:
        kwargs["date_created_from"] = date_from
    if date_to is not None:
        kwargs["date_created_to"] = date_to
    if limit is not None:
        kwargs["limit"] = limit

    try:
        # .list() walks pagination internally, so long runs are handled.
        executions = flow.executions.list(**kwargs)
    except TwilioRestException as exc:
        raise FetchError(
            f"Could not list executions for {flow_id}: "
            f"HTTP {exc.status} (code {exc.code}): {exc.msg}"
        ) from exc

    logger.info("Fetched %d execution(s) for flow %s", len(executions), flow_id)

    rows: list[dict[str, Any]] = []
    expired_contexts = 0
    transient_failures = 0

    for execution in executions:
        row: dict[str, Any] = {
            "execution_sid": execution.sid,
            "contact": execution.contact_channel_address,
            "status": execution.status,
            "date_created": execution.date_created,
            "date_updated": execution.date_updated,
        }

        if include_answers:
            # Three outcomes, not two. A 404 is a context that aged out of
            # retention - expected, and nothing can be done. A 429 or 5xx means
            # Twilio throttled or faulted and the answers still exist. Anything
            # else (401 after a token rotation, 403) is a configuration problem.
            # Folding all three into "may have aged out" is how a throttled run
            # produces a CSV with missing answers, blames Twilio's retention
            # window, and exits 0 - during the exact window when reconciliation
            # is supposed to be catching gaps.
            try:
                context = _fetch_context(flow, execution.sid)
                row.update(_widget_answers(context.context or {}))
            except (TwilioRestException, OSError) as exc:
                status = getattr(exc, "status", None)
                row[CONTEXT_ERROR_COLUMN] = (
                    f"HTTP {status}" if status is not None else type(exc).__name__
                )

                if status == 404:
                    expired_contexts += 1
                    logger.debug(
                        "No context for %s (%s): aged out",
                        execution.sid,
                        mask_phone(str(execution.contact_channel_address or "")),
                    )
                else:
                    transient_failures += 1
                    logger.error(
                        "Could not read context for %s (%s): %s. The answers "
                        "still exist; this row is incomplete.",
                        execution.sid,
                        mask_phone(str(execution.contact_channel_address or "")),
                        row[CONTEXT_ERROR_COLUMN],
                    )

        rows.append(row)

    if expired_contexts:
        logger.warning(
            "%d execution(s) had no retrievable context; they have aged out of "
            "Twilio's ~30-day retention window.",
            expired_contexts,
        )

    if transient_failures:
        # Reported, not raised. Throwing away every row already fetched would be
        # the wrong trade for this failure in particular: the trigger is usually
        # rate limiting, and re-running restarts from the first execution and
        # re-issues every request, which makes the cause worse. The rows are
        # kept, the affected ones carry `context_error`, and the caller decides
        # what to do about an incomplete reconciliation.
        logger.error(
            "%d execution(s) could not be read after retries. Their answers are "
            "missing from this output, which is NOT a complete reconciliation. "
            "Affected rows carry a %r column; narrow the window with --since / "
            "--until and re-run those.",
            transient_failures,
            CONTEXT_ERROR_COLUMN,
        )

    if not rows:
        return pd.DataFrame(columns=_BASE_COLUMNS)

    frame = pd.DataFrame(rows)

    # Keep execution metadata first, then whatever widgets turned up.
    ordered = _BASE_COLUMNS + [c for c in frame.columns if c not in _BASE_COLUMNS]
    return frame[ordered]


def reconcile(
    *, executions: pd.DataFrame, sheet: pd.DataFrame, sheet_key: str
) -> pd.DataFrame:
    """Find executions that never made it into the published dataset.

    Args:
        executions: Output of :func:`fetch_executions`.
        sheet: The published dataset - a MotherDuck export, or a Google Sheet
            downloaded as CSV. The parameter keeps its historical name.
        sheet_key: The column holding the execution SID or contact number to
            match on.

    Returns:
        The rows of ``executions`` with no counterpart in the published data -
        the responses the publish Function failed to write.

    Raises:
        FetchError: If the named key column is not in the published dataset.

    """
    if sheet_key not in sheet.columns:
        raise FetchError(
            f"Column {sheet_key!r} not in the sheet. "
            f"Available: {', '.join(map(str, sheet.columns))}"
        )

    match_column = "execution_sid" if sheet_key.lower().endswith("sid") else "contact"
    present = set(sheet[sheet_key].dropna().astype(str).str.strip())
    missing = executions[~executions[match_column].astype(str).isin(present)]

    if missing.empty:
        logger.info("Reconciliation clean: every execution is present in the sheet.")
    else:
        logger.warning(
            "%d execution(s) are missing from the sheet - publish_gsheets.js "
            "likely failed for these respondents.",
            len(missing),
        )

    return missing


def write_output(frame: pd.DataFrame, destination: Path) -> Path:
    """Write fetched executions to disk, warning about the contents."""
    # utf-8-sig so Excel does not render non-Latin answers as mojibake; see the
    # same choice in `decryptor.decrypt_dataset`.
    frame.to_csv(destination, index=False, encoding="utf-8-sig")
    logger.warning(
        "%s contains plain-text responses straight from Twilio. Store it per "
        "IPA policy and do not commit it.",
        destination,
    )
    return destination
