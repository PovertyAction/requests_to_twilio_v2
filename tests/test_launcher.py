"""Tests for the launcher, especially its crash-recovery behaviour."""

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from twilio.base.exceptions import TwilioRestException

from requests_to_twilio.launcher import (
    DeliveryRecord,
    LaunchError,
    already_sent,
    launch,
    read_input,
    tracker_path,
)


@pytest.fixture
def sample(tmp_path) -> Path:
    path = tmp_path / "sample.xlsx"
    pd.DataFrame(
        {
            "Number": ["whatsapp:+15555550100", "whatsapp:+15555550101"],
            "name": ["Ana", "Beto"],
            "city": ["Cali", "Bogota"],
        }
    ).to_excel(path, index=False)
    return path


def fake_client(*, statuses=("active", "active"), errors=()) -> MagicMock:
    """Build a Twilio client stand-in.

    Args:
        statuses: Status to report for each successive successful call.
        errors: Exceptions to raise, one per call, before falling back to
            statuses. A ``None`` entry means "succeed this time".

    """
    client = MagicMock()
    executions = client.studio.v2.flows.return_value.executions

    calls = {"n": 0}

    def create(**kwargs):
        index = calls["n"]
        calls["n"] += 1
        if index < len(errors) and errors[index] is not None:
            raise errors[index]
        result = MagicMock()
        result.status = statuses[min(index, len(statuses) - 1)]
        result.sid = f"FN{index:032d}"
        result.contact_channel_address = kwargs.get("to")
        result.url = f"https://studio.twilio.com/v2/Executions/FN{index}"
        return result

    executions.create.side_effect = create
    return client


class TestReadInput:
    def test_reads_excel(self, sample):
        assert len(read_input(sample, ["name"])) == 2

    def test_missing_file(self, tmp_path):
        with pytest.raises(LaunchError, match="not found"):
            read_input(tmp_path / "nope.xlsx", [])

    def test_missing_number_column(self, tmp_path):
        path = tmp_path / "bad.csv"
        pd.DataFrame({"phone": ["+1"]}).to_csv(path, index=False)
        with pytest.raises(LaunchError, match="no 'Number' column"):
            read_input(path, [])

    def test_missing_requested_column_fails_before_sending(self, sample):
        """A typo must be caught up front, not after 300 messages have gone."""
        with pytest.raises(LaunchError, match="citty"):
            read_input(sample, ["citty"])

    def test_unsupported_format(self, tmp_path):
        path = tmp_path / "data.txt"
        path.write_text("nope")
        with pytest.raises(LaunchError, match="Unsupported"):
            read_input(path, [])

    def test_blank_numbers_dropped(self, tmp_path):
        path = tmp_path / "blanks.csv"
        pd.DataFrame({"Number": ["+15555550100", None]}).to_csv(path, index=False)
        assert len(read_input(path, [])) == 1

    def test_all_blank_numbers_rejected(self, tmp_path):
        path = tmp_path / "empty.csv"
        pd.DataFrame({"Number": [None, None]}).to_csv(path, index=False)
        with pytest.raises(LaunchError, match="No usable rows"):
            read_input(path, [])


class TestLaunch:
    def test_sends_to_every_number(self, sample):
        client = fake_client()
        tracker = launch(
            client=client,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=["name", "city"],
            batch_size=10,
            sec_between_batches=0,
        )

        assert client.studio.v2.flows.return_value.executions.create.call_count == 2
        rows = pd.read_csv(tracker)
        assert len(rows) == 2
        assert set(rows["status"]) == {"active"}

    def test_parameters_are_passed_through(self, sample):
        client = fake_client()
        launch(
            client=client,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=["name", "city"],
            batch_size=10,
            sec_between_batches=0,
        )
        _, kwargs = (
            client.studio.v2.flows.return_value.executions.create.call_args_list[0]
        )
        parameters = kwargs["parameters"]
        assert parameters["name"] == "Ana"
        assert parameters["city"] == "Cali"
        # Always supplied on top of the requested columns: it is the only UTC
        # clock the flow has, since Studio stamps in Twilio's timezone.
        assert parameters["sent_at"].endswith("+00:00")
        assert set(parameters) == {"name", "city", "sent_at"}

    def test_dry_run_sends_nothing(self, sample):
        client = fake_client()
        launch(
            client=client,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=["name"],
            batch_size=10,
            sec_between_batches=0,
            dry_run=True,
        )
        client.studio.v2.flows.return_value.executions.create.assert_not_called()
        assert not tracker_path(sample).exists()

    def test_failure_is_recorded_not_raised(self, sample):
        """One bad number must not abort the run for everyone else."""
        error = TwilioRestException(status=400, uri="/x", msg="Invalid 'To' number")
        client = fake_client(errors=(error, None))

        tracker = launch(
            client=client,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=[],
            batch_size=10,
            sec_between_batches=0,
        )

        rows = pd.read_csv(tracker)
        assert len(rows) == 2
        assert rows.iloc[0]["status"] == "failed"
        assert "Invalid 'To' number" in rows.iloc[0]["error"]
        assert rows.iloc[1]["status"] == "active"

    def test_tracker_is_written_incrementally(self, sample):
        """The tracker must be on disk before the run ends.

        This is the property that made the pre-2.0 launcher dangerous: it built
        the whole spreadsheet in memory, so an interrupted run left no record of
        which respondents had already been messaged.
        """
        seen = []
        client = fake_client()
        original = client.studio.v2.flows.return_value.executions.create.side_effect

        def spy(**kwargs):
            seen.append(
                tracker_path(sample).read_text()
                if tracker_path(sample).exists()
                else ""
            )
            return original(**kwargs)

        client.studio.v2.flows.return_value.executions.create.side_effect = spy

        launch(
            client=client,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=[],
            batch_size=10,
            sec_between_batches=0,
        )

        # By the time the second send starts, the first row is already durable.
        assert "whatsapp:+15555550100" in seen[1]

    def test_resume_skips_already_sent(self, sample):
        client = fake_client()
        launch(
            client=client,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=[],
            batch_size=10,
            sec_between_batches=0,
        )

        second = fake_client()
        launch(
            client=second,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=[],
            batch_size=10,
            sec_between_batches=0,
            resume=True,
        )
        second.studio.v2.flows.return_value.executions.create.assert_not_called()

    def test_resume_retries_only_failures(self, sample):
        error = TwilioRestException(status=500, uri="/x", msg="server error")
        # Four attempts are made for the retryable 500 before it is recorded.
        client = fake_client(errors=(error, error, error, error, None))
        launch(
            client=client,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=[],
            batch_size=10,
            sec_between_batches=0,
        )

        second = fake_client()
        launch(
            client=second,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=sample,
            columns_to_send=[],
            batch_size=10,
            sec_between_batches=0,
            resume=True,
        )
        # Only the number that failed is retried.
        assert second.studio.v2.flows.return_value.executions.create.call_count == 1


class TestAlreadySent:
    def test_missing_tracker_is_empty(self, tmp_path):
        assert already_sent(tmp_path / "none.csv") == set()

    def test_only_successful_rows_counted(self, tmp_path):
        path = tmp_path / "tracker.csv"
        pd.DataFrame(
            {
                "number": ["+1", "+2", "+3"],
                "status": ["active", "failed", "ended"],
                "execution_sid": ["", "", ""],
                "contact": ["", "", ""],
                "url": ["", "", ""],
                "error": ["", "", ""],
                "sent_at": ["", "", ""],
            }
        ).to_csv(path, index=False)
        assert already_sent(path) == {"+1", "+3"}


def test_delivery_record_column_order():
    row = DeliveryRecord(number="+1").as_row()
    assert list(row) == [
        "number",
        "status",
        "execution_sid",
        "contact",
        "url",
        "error",
        "sent_at",
    ]


class TestSentAtParameter:
    """The launcher supplies the one UTC clock the flow can trust.

    Studio renders `now` in Twilio's own timezone and Liquid cannot convert it,
    so a timestamp the flow stamps itself is not comparable with anything. The
    send time therefore comes from outside.
    """

    def test_sent_at_is_passed_to_the_flow(self, tmp_path, monkeypatch):
        import pandas as pd
        from twilio.rest import Client

        from requests_to_twilio import launcher as mod

        sample = tmp_path / "s.xlsx"
        pd.DataFrame([{"Number": "+15550100", "caseid": "A"}]).to_excel(
            sample, index=False
        )

        seen = {}

        def fake_create(client, flow_id, to_number, from_number, parameters):
            seen.update(parameters)
            return type(
                "E",
                (),
                {
                    "status": "active",
                    "sid": "FN1",
                    "contact_channel_address": to_number,
                    "url": "",
                },
            )()

        monkeypatch.setattr(mod, "_create_execution", fake_create)

        mod.launch(
            client=object.__new__(Client),
            flow_id="FW1",
            from_number="whatsapp:+15550199",
            input_file=sample,
            columns_to_send=["caseid"],
            batch_size=50,
            sec_between_batches=0,
        )

        assert mod.SENT_AT_PARAM in seen
        assert seen[mod.SENT_AT_PARAM].endswith("+00:00")
        assert seen["caseid"] == "A"

    def test_it_matches_the_tracker_exactly(self, tmp_path, monkeypatch):
        """The delivery tracker and the published row must agree on the time."""
        import csv

        import pandas as pd
        from twilio.rest import Client

        from requests_to_twilio import launcher as mod

        sample = tmp_path / "s.xlsx"
        pd.DataFrame([{"Number": "+15550100", "caseid": "A"}]).to_excel(
            sample, index=False
        )
        seen = {}

        def fake_create(client, flow_id, to_number, from_number, parameters):
            seen.update(parameters)
            return type(
                "E",
                (),
                {
                    "status": "active",
                    "sid": "FN1",
                    "contact_channel_address": to_number,
                    "url": "",
                },
            )()

        monkeypatch.setattr(mod, "_create_execution", fake_create)
        tracker = mod.launch(
            client=object.__new__(Client),
            flow_id="FW1",
            from_number="whatsapp:+15550199",
            input_file=sample,
            columns_to_send=["caseid"],
            batch_size=50,
            sec_between_batches=0,
        )
        with tracker.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        assert row["sent_at"] == seen[mod.SENT_AT_PARAM]
