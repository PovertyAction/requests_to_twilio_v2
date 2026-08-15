"""Tests for the delivery monitor.

The monitor exists to see the failures nothing else can: a message that never
became an execution, and a reply that Twilio accepted and could not hand over.
Both look like success everywhere else, so the tests here are mostly about the
monitor refusing to report success.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest
from twilio.base.exceptions import TwilioRestException

from requests_to_twilio.monitor import (
    LOG_COLUMNS,
    NUMBER_COLUMNS,
    MonitorError,
    by_number,
    launch_failures,
    launch_window,
    pending,
    poll_delivery,
    read_tracker,
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


def tracker(*records, path=None):
    """Write a delivery tracker the way `rtt launch` does.

    Keyed on caseid: the tracker holds no phone number, so there is nothing in
    it to mask. The first field of each record is the respondent's caseid.
    """
    frame = pd.DataFrame(
        records, columns=["caseid", "status", "execution_sid", "error", "sent_at"]
    )
    if path is not None:
        frame.to_csv(path, index=False)
    return frame


class TestScopingToARound:
    """A date is the wrong unit for a window.

    On the first live round of this instrument, `--since` at day resolution
    returned 91 messages for a round of 4 - the account's whole day, including
    traffic from unrelated flows. The tracker knows when the launch happened.
    """

    def test_the_window_starts_just_before_the_first_send(self):
        frame = tracker(
            ("+15555550100", "active", "FN1", "", "2026-08-14T21:20:14+00:00"),
            ("+15555550101", "active", "FN2", "", "2026-08-14T21:20:16+00:00"),
        )
        started = launch_window(frame)
        # The earliest send, less the margin - `sent_at` and Twilio's `date_sent`
        # can disagree by a moment, and the first message must not fall outside
        # its own round.
        assert started == datetime(2026, 8, 14, 21, 19, 14, tzinfo=UTC)

    def test_a_tracker_with_no_usable_stamp_has_no_window(self):
        assert launch_window(tracker(("+1", "failed", "", "HTTP 400", ""))) is None

    def test_a_file_that_is_not_a_tracker_is_refused(self, tmp_path):
        path = tmp_path / "not_a_tracker.csv"
        pd.DataFrame({"a": [1]}).to_csv(path, index=False)
        with pytest.raises(MonitorError, match="does not look like a delivery tracker"):
            read_tracker(path)

    def test_a_missing_file_is_reported_as_itself(self, tmp_path):
        with pytest.raises(MonitorError, match="Could not read"):
            read_tracker(tmp_path / "nope.csv")


class TestSendsThatNeverLeft:
    """The rows no other view can show.

    The API refused or Meta rejected, so there is no execution, no message with a
    delivery status, and no published row. The respondent is absent from the
    dataset rather than incomplete in it, and this file is the only record that
    they were contacted at all.
    """

    def test_a_failed_send_is_reported(self):
        frame = tracker(
            ("+15555550100", "failed", "", "HTTP 400 (code 63016)", ""),
            ("+15555550101", "active", "FN2", "", "2026-08-14T21:20:16+00:00"),
        )
        found = launch_failures(frame)
        assert len(found) == 1
        assert "63016" in found.iloc[0]["error"]

    def test_the_failure_is_named_by_caseid_and_carries_no_number(self):
        # This used to mask a `number` column. There is no longer one to mask:
        # the tracker is keyed on caseid, so a phone number cannot reach it.
        frame = tracker(("A1", "failed", "", "HTTP 400", ""))
        found = launch_failures(frame)
        assert found.iloc[0]["caseid"] == "A1"
        assert "number" not in found.columns

    def test_a_clean_launch_reports_nothing(self):
        frame = tracker(
            ("+15555550100", "active", "FN1", "", "2026-08-14T21:20:14+00:00")
        )
        assert launch_failures(frame).empty


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


def sends(*specs):
    """Build a per-message frame as (sid, direction, status, error, sent)."""
    return rows(
        *[
            (
                sid,
                "whatsapp:+15555550100"
                if direction.startswith("outbound")
                else "whatsapp:+15555550199",
                "whatsapp:+15555550199"
                if direction.startswith("outbound")
                else "whatsapp:+15555550100",
                direction,
                status,
                error,
                "",
                sent,
                sent,
                "polled",
            )
            for sid, direction, status, error, sent in specs
        ]
    )


class TestOneRowPerNumber:
    """One state per number: did it land, and did they answer.

    A round of 4 produced 71 messages. Nobody watching a live round wants 71
    rows; they want to know which of the 4 got it and who has gone quiet.
    """

    def test_a_reply_outranks_everything(self):
        """A reply proves delivery better than a delivery receipt does."""
        frame = sends(
            ("SM1", "outbound-api", "sent", "", "2026-08-14T21:20:00+00:00"),
            ("SM2", "inbound", "received", "", "2026-08-14T21:21:00+00:00"),
        )
        assert by_number(frame).iloc[0]["delivery_status"] == "answered_back"

    def test_a_failed_opener_is_failed(self):
        frame = sends(
            ("SM1", "outbound-api", "undelivered", "63016", "2026-08-14T21:20:00+00:00")
        )
        assert by_number(frame).iloc[0]["delivery_status"] == "failed"

    def test_delivered_and_read_are_both_delivered(self):
        for status in ("delivered", "read"):
            frame = sends(
                ("SM1", "outbound-api", status, "", "2026-08-14T21:20:00+00:00")
            )
            assert by_number(frame).iloc[0]["delivery_status"] == "delivered"

    def test_anything_else_is_still_only_sent(self):
        frame = sends(
            ("SM1", "outbound-api", "queued", "", "2026-08-14T21:20:00+00:00")
        )
        assert by_number(frame).iloc[0]["delivery_status"] == "sent"

    def test_only_the_opener_decides(self):
        """A later hiccup must not erase that the round reached them.

        Messages after the first exist because the respondent was already
        engaging, so folding them in would let question 4 failing overwrite the
        fact that the opener landed.
        """
        frame = sends(
            ("SM1", "outbound-api", "delivered", "", "2026-08-14T21:20:00+00:00"),
            ("SM2", "outbound-api", "undelivered", "", "2026-08-14T21:25:00+00:00"),
        )
        assert by_number(frame).iloc[0]["delivery_status"] == "delivered"

    def test_the_respondent_is_not_the_sending_address(self):
        """Grouping on `to` files every reply under the Twilio number."""
        frame = sends(
            ("SM1", "outbound-api", "delivered", "", "2026-08-14T21:20:00+00:00"),
            ("SM2", "inbound", "received", "", "2026-08-14T21:21:00+00:00"),
        )
        result = by_number(frame, {"15555550100": "A1"})
        assert len(result) == 1
        assert result.iloc[0]["caseid"] == "A1"

    def test_the_number_is_resolved_to_a_caseid_and_never_written(self):
        frame = sends(
            ("SM1", "outbound-api", "delivered", "", "2026-08-14T21:20:00+00:00")
        )
        result = by_number(frame, {"15555550100": "A1"})
        assert result.iloc[0]["caseid"] == "A1"
        # The whole point: nothing written carries the number in any column.
        assert "5555550100" not in result.astype(str).to_csv(index=False)

    def test_the_master_list_matches_across_spellings(self):
        # The list holds "+1 555 555 0100"; the API returns
        # "whatsapp:+15555550100". Comparing them literally matches neither.
        frame = sends(
            ("SM1", "outbound-api", "delivered", "", "2026-08-14T21:20:00+00:00")
        )
        assert by_number(frame, {"15555550100": "A1"}).iloc[0]["caseid"] == "A1"

    def test_a_number_the_master_list_does_not_know_gets_a_stable_stand_in(self):
        # Somebody writing in unprompted is worth seeing, not dropping - but
        # they have no caseid, and the stand-in must not be reversible to a
        # phone number by anyone reading the log or the sheet.
        frame = sends(("SM1", "inbound", "received", "", "2026-08-14T21:20:00+00:00"))
        first = by_number(frame, {}).iloc[0]["caseid"]
        second = by_number(frame, {}).iloc[0]["caseid"]

        assert first.startswith("unknown-")
        assert first == second, "the key must not change between polls"
        assert "5555550100" not in first

    def test_an_inbound_error_survives_into_the_row(self):
        """Error 11200 means their answer reached Twilio and not the flow."""
        frame = sends(
            ("SM1", "outbound-api", "delivered", "", "2026-08-14T21:20:00+00:00"),
            ("SM2", "inbound", "received", "11200", "2026-08-14T21:21:00+00:00"),
        )
        assert by_number(frame).iloc[0]["error_codes"] == "11200"

    def test_inbound_only_is_unsolicited(self):
        """Somebody wrote in without being launched."""
        frame = sends(("SM1", "inbound", "received", "", "2026-08-14T21:20:00+00:00"))
        assert by_number(frame).iloc[0]["delivery_status"] == "unsolicited"


class TestSettledNumbersStopBeingPolled:
    def test_pending_excludes_the_settled(self):
        log = pd.DataFrame(
            {"delivery_status": ["sent", "delivered", "answered_back", "failed"]}
        )
        assert set(pending(log)["delivery_status"]) == {"sent", "delivered"}

    def test_a_settled_row_is_never_walked_back(self, tmp_path):
        """A later poll with a narrower window must not undo a settled state.

        It might not see the reply that settled the row, which would reset it to
        `sent` and put it back in the pending set forever.
        """
        path = tmp_path / "log.csv"
        update_log(
            by_number(
                sends(
                    ("SM1", "outbound-api", "sent", "", "2026-08-14T21:20:00+00:00"),
                    ("SM2", "inbound", "received", "", "2026-08-14T21:21:00+00:00"),
                )
            ),
            path,
        )
        # A narrower poll that only sees the opener.
        added, changed = update_log(
            by_number(
                sends(("SM1", "outbound-api", "sent", "", "2026-08-14T21:20:00+00:00"))
            ),
            path,
        )
        assert (added, changed) == (0, 0)
        assert pd.read_csv(path).loc[0, "delivery_status"] == "answered_back"

    def test_a_state_that_moves_forward_is_written(self, tmp_path):
        path = tmp_path / "log.csv"
        update_log(
            by_number(
                sends(("SM1", "outbound-api", "sent", "", "2026-08-14T21:20:00+00:00"))
            ),
            path,
        )
        added, changed = update_log(
            by_number(
                sends(
                    (
                        "SM1",
                        "outbound-api",
                        "delivered",
                        "",
                        "2026-08-14T21:20:00+00:00",
                    )
                )
            ),
            path,
        )
        assert (added, changed) == (0, 1)
        assert pd.read_csv(path).loc[0, "delivery_status"] == "delivered"

    def test_a_number_outside_the_window_survives(self, tmp_path):
        path = tmp_path / "log.csv"
        update_log(
            by_number(
                sends(("SM1", "outbound-api", "sent", "", "2026-08-14T21:20:00+00:00"))
            ),
            path,
        )
        other = sends(("SM9", "outbound-api", "sent", "", "2026-08-14T22:00:00+00:00"))
        other["to"] = "whatsapp:+15555550777"
        update_log(by_number(other), path)
        assert len(pd.read_csv(path)) == 2

    def test_an_empty_poll_leaves_the_file_alone(self, tmp_path):
        path = tmp_path / "log.csv"
        update_log(
            by_number(
                sends(("SM1", "outbound-api", "sent", "", "2026-08-14T21:20:00+00:00"))
            ),
            path,
        )
        assert update_log(pd.DataFrame(columns=NUMBER_COLUMNS), path) == (0, 0)
        assert len(pd.read_csv(path)) == 1
