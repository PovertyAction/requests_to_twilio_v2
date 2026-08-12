"""Pull flow executions from the Twilio Studio API, for reconciliation.

The Google Sheet is the database of record, but the function that writes to it
can fail: a Sheets API error that outlives its retries drops that respondent's
row silently, and nothing downstream notices. This module fetches what Twilio
believes happened so the two can be compared, and so a dropped response can be
recovered.

Two things to know before using it:

* Twilio retains execution context for a limited window (30 days at the time of
  writing). Reconcile during collection, not months later.
* The context holds the answers **as the respondent sent them**, in plain text.
  The encryption in this project protects the copy that lands in Google Sheets,
  which is the widely-shared surface; it was never able to protect the copy
  inside Twilio. Output from this command is therefore unencrypted PII.

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
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .log import get_logger, mask_phone

logger = get_logger()

#: Execution-level fields captured for every row.
_BASE_COLUMNS = ["execution_sid", "contact", "status", "date_created", "date_updated"]


class FetchError(Exception):
    """Raised when executions cannot be retrieved."""


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
    context_failures = 0

    for execution in executions:
        row: dict[str, Any] = {
            "execution_sid": execution.sid,
            "contact": execution.contact_channel_address,
            "status": execution.status,
            "date_created": execution.date_created,
            "date_updated": execution.date_updated,
        }

        if include_answers:
            try:
                context = flow.executions(execution.sid).execution_context().fetch()
                row.update(_widget_answers(context.context or {}))
            except TwilioRestException as exc:
                # A context can expire out of retention while its execution
                # record remains. Keep the row; note the gap.
                context_failures += 1
                logger.debug(
                    "No context for %s (%s): HTTP %s",
                    execution.sid,
                    mask_phone(str(execution.contact_channel_address or "")),
                    exc.status,
                )

        rows.append(row)

    if context_failures:
        logger.warning(
            "%d execution(s) had no retrievable context; they may have aged out "
            "of Twilio's retention window.",
            context_failures,
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
    """Find executions that never made it into the Google Sheet.

    Args:
        executions: Output of :func:`fetch_executions`.
        sheet: The dataset downloaded from Google Sheets.
        sheet_key: The sheet column holding the execution SID or contact number
            to match on.

    Returns:
        The rows of ``executions`` with no counterpart in the sheet - the
        responses that ``publish_gsheets.js`` failed to append.

    Raises:
        FetchError: If the named key column is not in the sheet.

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
    frame.to_csv(destination, index=False)
    logger.warning(
        "%s contains plain-text responses straight from Twilio. Store it per "
        "IPA policy and do not commit it.",
        destination,
    )
    return destination
