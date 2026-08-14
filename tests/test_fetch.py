"""Tests for pulling executions back out of Twilio.

Reconciliation is the check of last resort: it runs when a row is already
suspected missing, during collection, while there is still time to re-contact
someone. So the way it reports its *own* failures matters more than usual. A
reconciliation that quietly returns short answers is worse than one that fails,
because the whole point of running it is to trust the count at the end.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest
from tenacity import wait_none
from twilio.base.exceptions import TwilioRestException

from requests_to_twilio import fetch as fetch_module
from requests_to_twilio.fetch import (
    CONTEXT_ERROR_COLUMN,
    FetchError,
    fetch_executions,
    reconcile,
)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Retry immediately instead of sleeping.

    Every retryable case here would otherwise wait 2 + 4 + 8 seconds for real.
    The retry *policy* is still exercised - the call is still attempted four
    times - only the delay between attempts is removed.
    """
    monkeypatch.setattr(fetch_module._fetch_context.retry, "wait", wait_none())


def execution(sid: str, contact: str = "whatsapp:+15555550100"):
    """Build an execution record as the SDK returns it."""
    record = MagicMock()
    record.sid = sid
    record.contact_channel_address = contact
    record.status = "ended"
    record.date_created = "2026-08-01T00:00:00Z"
    record.date_updated = "2026-08-01T00:10:00Z"
    return record


def rest_error(status: int) -> TwilioRestException:
    return TwilioRestException(status=status, uri="/x", msg="boom", code=20001)


def fake_client(executions, *, context_errors=None):
    """Build a Twilio client stand-in.

    Args:
        executions: Execution records to return from `.list()`.
        context_errors: Per-SID exception to raise from the context fetch. A
            SID that is absent returns answers normally.

    """
    context_errors = context_errors or {}
    client = MagicMock()
    flow = client.studio.v2.flows.return_value
    flow.executions.list.return_value = executions

    def executions_by_sid(sid):
        handle = MagicMock()
        error = context_errors.get(sid)
        if error is not None:
            handle.execution_context.return_value.fetch.side_effect = error
        else:
            context = MagicMock()
            context.context = {
                "widgets": {"q1": {"inbound": {"Body": f"answer from {sid}"}}}
            }
            handle.execution_context.return_value.fetch.return_value = context
        return handle

    flow.executions.side_effect = executions_by_sid
    return client


class TestListing:
    def test_rows_are_returned_for_every_execution(self):
        client = fake_client([execution("FN1"), execution("FN2")])
        frame = fetch_executions(client=client, flow_id="FW1")
        assert list(frame["execution_sid"]) == ["FN1", "FN2"]

    def test_a_failed_listing_is_an_error(self):
        client = MagicMock()
        client.studio.v2.flows.return_value.executions.list.side_effect = rest_error(
            401
        )
        with pytest.raises(FetchError, match="Could not list executions"):
            fetch_executions(client=client, flow_id="FW1")

    def test_no_executions_yields_an_empty_frame_with_columns(self):
        frame = fetch_executions(client=fake_client([]), flow_id="FW1")
        assert frame.empty
        assert "execution_sid" in frame.columns

    def test_answers_can_be_skipped(self):
        """One context call per execution is the expensive part."""
        client = fake_client([execution("FN1")])
        fetch_executions(client=client, flow_id="FW1", include_answers=False)
        assert client.studio.v2.flows.return_value.executions.called is False


class TestContextFailureClassification:
    """A 404 and a 429 mean opposite things and must not be merged.

    404 is a context that aged out of retention: expected, unrecoverable, and
    nothing to do about it. 429 or 5xx means Twilio throttled or faulted and the
    answers still exist. Reporting the second as the first is how a throttled
    run produces a CSV with missing answers, blames Twilio's retention window,
    and exits 0.
    """

    def test_expired_context_is_not_marked_as_an_error(self):
        client = fake_client(
            [execution("FN1")], context_errors={"FN1": rest_error(404)}
        )
        frame = fetch_executions(client=client, flow_id="FW1")
        assert frame.loc[0, CONTEXT_ERROR_COLUMN] == "HTTP 404"

    def test_a_throttled_context_still_returns_its_row(self):
        """The rows already fetched must survive.

        Discarding them would be the wrong trade for this failure in
        particular: the trigger is rate limiting, and re-running restarts from
        the first execution and re-issues every request.
        """
        client = fake_client(
            [execution("FN1"), execution("FN2")],
            context_errors={"FN2": rest_error(429)},
        )
        frame = fetch_executions(client=client, flow_id="FW1")

        assert len(frame) == 2
        assert frame.loc[1, CONTEXT_ERROR_COLUMN] == "HTTP 429"

    def test_a_successful_row_carries_no_error_marker(self):
        client = fake_client(
            [execution("FN1"), execution("FN2")],
            context_errors={"FN2": rest_error(429)},
        )
        frame = fetch_executions(client=client, flow_id="FW1")
        assert pd.isna(frame.loc[0, CONTEXT_ERROR_COLUMN])

    def test_an_auth_failure_is_not_blamed_on_retention(self):
        """A rotated token is a configuration problem, not an expiry."""
        client = fake_client(
            [execution("FN1")], context_errors={"FN1": rest_error(401)}
        )
        frame = fetch_executions(client=client, flow_id="FW1")
        assert frame.loc[0, CONTEXT_ERROR_COLUMN] == "HTTP 401"

    def test_a_dropped_connection_does_not_escape(self):
        """`is_retryable` opts into retrying OSError, so it must be handled."""
        client = fake_client(
            [execution("FN1")], context_errors={"FN1": ConnectionError("reset")}
        )
        frame = fetch_executions(client=client, flow_id="FW1")
        assert frame.loc[0, CONTEXT_ERROR_COLUMN] == "ConnectionError"

    def test_a_transient_failure_is_retried_before_being_given_up_on(self):
        attempts = {"count": 0}

        def count_and_raise(*_args, **_kwargs):
            attempts["count"] += 1
            raise rest_error(429)

        client = MagicMock()
        flow = client.studio.v2.flows.return_value
        flow.executions.list.return_value = [execution("FN1")]
        handle = MagicMock()
        handle.execution_context.return_value.fetch.side_effect = count_and_raise
        flow.executions.return_value = handle

        fetch_executions(client=client, flow_id="FW1")

        assert attempts["count"] == 4

    def test_a_permanent_failure_is_not_retried(self):
        """Retrying a 404 just delays a conclusion that will not change."""
        attempts = {"count": 0}

        def count_and_raise(*_args, **_kwargs):
            attempts["count"] += 1
            raise rest_error(404)

        client = MagicMock()
        flow = client.studio.v2.flows.return_value
        flow.executions.list.return_value = [execution("FN1")]
        handle = MagicMock()
        handle.execution_context.return_value.fetch.side_effect = count_and_raise
        flow.executions.return_value = handle

        fetch_executions(client=client, flow_id="FW1")

        assert attempts["count"] == 1

    def test_a_status_of_none_does_not_crash_the_run(self):
        """`None >= 500` is a TypeError, raised from inside the handler."""
        client = fake_client(
            [execution("FN1")], context_errors={"FN1": rest_error(None)}
        )
        frame = fetch_executions(client=client, flow_id="FW1")
        assert len(frame) == 1


class TestReconcile:
    def test_missing_rows_are_returned(self):
        executions = pd.DataFrame(
            {"execution_sid": ["FN1", "FN2"], "contact": ["a", "b"]}
        )
        published = pd.DataFrame({"execution_sid": ["FN1"]})
        missing = reconcile(
            executions=executions, sheet=published, sheet_key="execution_sid"
        )
        assert list(missing["execution_sid"]) == ["FN2"]

    def test_an_unknown_key_column_is_an_error(self):
        executions = pd.DataFrame({"execution_sid": ["FN1"]})
        with pytest.raises(FetchError, match="nope"):
            reconcile(
                executions=executions,
                sheet=pd.DataFrame({"execution_sid": ["FN1"]}),
                sheet_key="nope",
            )
