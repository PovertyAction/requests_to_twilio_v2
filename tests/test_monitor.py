"""Tests for the delivery monitor.

The monitor exists to see the failures nothing else can: a message that never
became an execution, and a reply that Twilio accepted and could not hand over.
Both look like success everywhere else, so the tests here are mostly about the
monitor refusing to report success.
"""

from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest
from twilio.base.exceptions import TwilioRestException

from requests_to_twilio.monitor import (
    LOG_COLUMNS,
    MonitorError,
    poll_delivery,
    problems,
    summarise,
    update_log,
)


def message(
    sid,
    status,
    *,
    to="whatsapp:+15555550100",
    direction="outbound-api",
    error_code=None,
    sent="2026-08-14T21:20:00+00:00",
):
    return SimpleNamespace(
        sid=sid,
        to=to,
        from_="whatsapp:+15555550199",
        direction=direction,
        status=status,
        error_code=error_code,
        error_message="" if error_code is None else f"error {error_code}",
        date_sent=datetime.fromisoformat(sent),
        date_updated=datetime.fromisoformat(sent),
    )


def client_with(*messages, error=None):
    def _list(**kwargs):
        if error is not None:
            raise error
        return list(messages)

    return SimpleNamespace(messages=SimpleNamespace(list=_list))


def rows(*specs):
    """Build a poll-shaped frame directly, for the pure functions."""
    return pd.DataFrame(
        [dict(zip(LOG_COLUMNS, spec, strict=False)) for spec in specs],
        columns=LOG_COLUMNS,
    )


class TestPolling:
    def test_a_rate_limit_is_not_an_empty_round(self):
        """The failure this module was written to refuse.

        Polling repeatedly is the point, and repeated polling is what earns a
        429. Reporting it as "no messages" would show a quiet, healthy round at
        exactly the moment the account is busiest.
        """
        throttled = TwilioRestException(429, "uri", "Too Many Requests", 20429)
        with pytest.raises(MonitorError, match="rate-limited"):
            poll_delivery(client=client_with(error=throttled))

    def test_the_rate_limit_message_says_the_data_is_still_there(self):
        throttled = TwilioRestException(429, "uri", "Too Many Requests", 20429)
        with pytest.raises(MonitorError) as caught:
            poll_delivery(client=client_with(error=throttled))
        assert "not an empty round" in str(caught.value)

    def test_another_api_error_is_reported_as_itself(self):
        denied = TwilioRestException(403, "uri", "Forbidden", 20003)
        with pytest.raises(MonitorError, match="HTTP 403"):
            poll_delivery(client=client_with(error=denied))

    def test_an_error_code_is_kept_verbatim(self):
        """It is the join key into Twilio's documentation."""
        frame = poll_delivery(
            client=client_with(message("SM1", "undelivered", error_code=63016))
        )
        assert frame.loc[0, "error_code"] == "63016"

    def test_no_messages_is_an_empty_frame_not_an_error(self):
        frame = poll_delivery(client=client_with())
        assert frame.empty
        assert list(frame.columns) == LOG_COLUMNS


class TestTheLogUpdatesInPlace:
    """One row per message however often it is polled."""

    def test_a_second_poll_does_not_duplicate(self, tmp_path):
        path = tmp_path / "log.csv"
        frame = rows(
            ("SM1", "to", "from", "outbound-api", "sent", "", "", "t", "t", "p")
        )
        update_log(frame, path)
        update_log(frame, path)
        assert len(pd.read_csv(path)) == 1

    def test_a_status_change_is_counted_and_written(self, tmp_path):
        path = tmp_path / "log.csv"
        update_log(
            rows(("SM1", "to", "f", "outbound-api", "sent", "", "", "t", "t", "p")),
            path,
        )
        added, changed = update_log(
            rows(("SM1", "to", "f", "outbound-api", "read", "", "", "t", "t", "p2")),
            path,
        )
        assert (added, changed) == (0, 1)
        assert pd.read_csv(path).loc[0, "status"] == "read"

    def test_an_unchanged_poll_reports_nothing_moved(self, tmp_path):
        """A quiet poll should say so rather than report every row as touched."""
        path = tmp_path / "log.csv"
        frame = rows(("SM1", "to", "f", "outbound-api", "sent", "", "", "t", "t", "p"))
        update_log(frame, path)
        assert update_log(frame, path) == (0, 0)

    def test_rows_outside_the_window_survive(self, tmp_path):
        """A narrow --since must not delete what it did not look at."""
        path = tmp_path / "log.csv"
        update_log(
            rows(("OLD", "to", "f", "outbound-api", "read", "", "", "a", "a", "p")),
            path,
        )
        update_log(
            rows(("NEW", "to", "f", "outbound-api", "sent", "", "", "b", "b", "p")),
            path,
        )
        assert set(pd.read_csv(path)["message_sid"]) == {"OLD", "NEW"}

    def test_an_empty_poll_leaves_the_file_alone(self, tmp_path):
        path = tmp_path / "log.csv"
        update_log(
            rows(("SM1", "to", "f", "outbound-api", "sent", "", "", "t", "t", "p")),
            path,
        )
        assert update_log(pd.DataFrame(columns=LOG_COLUMNS), path) == (0, 0)
        assert len(pd.read_csv(path)) == 1


class TestProblemsCatchesBothDirections:
    """Reporting only outbound failures was this function's own first bug."""

    def test_an_outbound_failure_is_found(self):
        frame = rows(
            (
                "SM1",
                "to",
                "f",
                "outbound-api",
                "undelivered",
                "63016",
                "no",
                "t",
                "t",
                "p",
            )
        )
        assert len(problems(frame)) == 1

    def test_an_inbound_error_is_found_even_though_it_says_received(self):
        """Error 11200 on an inbound message: the reply arrived and was dropped.

        Status `received` reads as success and the message did reach Twilio -
        what failed is the webhook handing it to the flow. Measured live: five
        replies lost this way while every other surface reported four sends and
        zero failures.
        """
        frame = rows(
            ("SM1", "to", "f", "inbound", "received", "11200", "", "t", "t", "p")
        )
        assert len(problems(frame)) == 1

    def test_a_clean_round_reports_nothing(self):
        frame = rows(
            ("SM1", "to", "f", "outbound-api", "delivered", "", "", "t", "t", "p"),
            ("SM2", "to", "f", "inbound", "received", "", "", "t", "t", "p"),
        )
        assert problems(frame).empty

    def test_numbers_are_masked_for_display(self):
        frame = rows(
            (
                "SM1",
                "whatsapp:+15555550100",
                "f",
                "inbound",
                "received",
                "11200",
                "",
                "t",
                "t",
                "p",
            )
        )
        assert "5555550100" not in problems(frame).loc[0, "to"]


class TestSummary:
    def test_anything_with_an_error_sorts_above_a_clean_status(self):
        """During a round, the only actionable rows go at the top."""
        frame = rows(
            *[
                (f"SM{i}", "to", "f", "inbound", "received", "", "", "t", "t", "p")
                for i in range(9)
            ],
            ("SMX", "to", "f", "inbound", "received", "11200", "", "t", "t", "p"),
        )
        assert summarise(frame).loc[0, "error_code"] == "11200"

    def test_an_empty_frame_summarises_to_nothing(self):
        assert summarise(pd.DataFrame(columns=LOG_COLUMNS)).empty
