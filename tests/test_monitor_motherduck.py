"""Tests for `rtt monitor --table`, the MotherDuck side of a live round.

`--sheet` mirrors each poll into a spreadsheet tab, which is the surface a room
watches. A round that publishes to MotherDuck had no equivalent: the delivery
state existed only as a local CSV on the laptop running the poll, so nobody who
was not at that terminal could see the round land.

The table is written with `CREATE OR REPLACE TABLE`, exactly as `replace_tab`
rewrites the tab, because what it holds is the current state of every number
rather than a log. That makes the target table destructive by design, which is
why pointing it at the table the flow publishes to is refused rather than
documented.
"""

import pandas as pd
import pytest
from typer.testing import CliRunner

from requests_to_twilio import cli
from requests_to_twilio.monitor import NUMBER_COLUMNS
from requests_to_twilio.warehouse import WarehouseError

runner = CliRunner()


def settled_row(caseid: str = "RST2026-TEST-001") -> pd.DataFrame:
    """One number, already settled, in the shape `update_log` merges."""
    row = dict.fromkeys(NUMBER_COLUMNS, "")
    row.update(
        {
            "caseid": caseid,
            "delivery_status": "answered_back",
            "outbound": "1",
            "inbound": "1",
            "error_codes": "",
            "first_sent": "2026-08-24T09:00:00+00:00",
            "last_activity": "2026-08-24T09:01:00+00:00",
            "polled_at": "2026-08-24T09:01:30+00:00",
        }
    )
    return pd.DataFrame([row])


@pytest.fixture
def warehouse(monkeypatch):
    """Build a poll loop with a fake clock and a recording `push_dataframe`."""
    state = {"now": 0.0, "writes": [], "fail_next": False}

    # `cfg.load_env` reads the repository's own .env, which on a developer's
    # machine sets MOTHERDUCK_DATABASE and MOTHERDUCK_TABLE. Left in place the
    # guard tests would pass or fail according to whose laptop ran them.
    monkeypatch.setattr(cli.cfg, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(cli, "Client", lambda *a, **k: object())
    monkeypatch.setattr(cli, "poll_delivery", lambda **k: pd.DataFrame())
    monkeypatch.setattr(cli, "by_number", lambda frame, caseids: settled_row())
    monkeypatch.setattr(cli.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(
        cli.time, "sleep", lambda seconds: state.update(now=state["now"] + seconds)
    )

    def fake_push(*, frame, table, database, mode="append", columns=None):
        if state["fail_next"]:
            state["fail_next"] = False
            raise WarehouseError("connection refused")
        state["writes"].append({"table": table, "database": database, "mode": mode})
        return len(frame)

    monkeypatch.setattr(cli, "push_dataframe", fake_push)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "0" * 32)
    monkeypatch.setenv("MOTHERDUCK_DATABASE", "rst_test")
    monkeypatch.delenv("MOTHERDUCK_TABLE", raising=False)
    return state


def run(tmp_path, *extra: str):
    """Invoke the monitor against a temp log, with no sheet and no sample."""
    return runner.invoke(
        cli.app,
        [
            "monitor",
            "--output",
            str(tmp_path / "log.csv"),
            "--since",
            "2026-08-24",
            *extra,
        ],
    )


def test_no_table_means_no_warehouse_write(tmp_path, warehouse):
    """The default touches nothing: a warehouse is opt-in, like the sheet."""
    result = run(tmp_path, "--hours", "1", "--every", "2")
    assert result.exit_code == 0, result.output
    assert warehouse["writes"] == []


def test_the_table_is_replaced_not_appended(tmp_path, warehouse):
    """`append` would add the whole round again on every poll.

    Everything the monitor holds is one row per number, rewritten from the log
    each time. Appending would grow the table by the size of the round every
    two minutes and leave the tab reading whatever the last poll happened to
    add.
    """
    result = run(tmp_path, "--hours", "1", "--every", "2", "--table", "tracking")
    assert result.exit_code == 0, result.output
    assert [w["mode"] for w in warehouse["writes"]] == ["replace"]
    assert warehouse["writes"][0]["table"] == "tracking"
    assert warehouse["writes"][0]["database"] == "rst_test"
    assert "table updated: 1 row(s) in tracking" in result.output


def test_every_poll_writes_so_the_table_keeps_moving(tmp_path, warehouse):
    """The reason `--full-window` exists applies to a table as much as a tab.

    Six polls in ten minutes, six writes. A table that stops being rewritten
    part-way through a window is the sheet-token failure in another costume.
    """
    result = run(
        tmp_path,
        "--hours",
        str(10 / 60),
        "--every",
        "2",
        "--full-window",
        "--table",
        "tracking",
    )
    assert result.exit_code == 0, result.output
    assert len(warehouse["writes"]) == 6
    assert {w["mode"] for w in warehouse["writes"]} == {"replace"}


def test_a_failed_write_reports_and_keeps_watching(tmp_path, warehouse):
    """The CSV is the record; a dead warehouse must not end the round.

    Same rule the sheet path follows. The write that fails says so on the
    terminal, and the next poll tries again.
    """
    warehouse["fail_next"] = True
    result = run(
        tmp_path,
        "--hours",
        str(6 / 60),
        "--every",
        "2",
        "--full-window",
        "--table",
        "tracking",
    )
    assert result.exit_code == 0, result.output
    assert "table not updated: connection refused" in result.output
    # Four polls in six minutes at two-minute intervals; the first raised.
    assert len(warehouse["writes"]) == 3


class TestItRefusesThePublishTarget:
    """`--table` replaces, so the flow's own table is the one thing it must not name.

    `CREATE OR REPLACE TABLE` on the publish target drops every submission the
    round has collected, then does it again on the next poll, and prints
    `table updated` each time. Nothing downstream would notice: the flow keeps
    returning 200, and the table exists and has rows in it - the monitor's.
    """

    def test_a_bare_name_matching_the_publish_target_is_refused(
        self, tmp_path, warehouse, monkeypatch
    ):
        """`MOTHERDUCK_TABLE` is qualified and `--table` is bare, but they collide."""
        monkeypatch.setenv("MOTHERDUCK_TABLE", "rst_test.main.data_use")
        result = run(tmp_path, "--hours", "1", "--table", "data_use")
        assert result.exit_code != 0
        assert "where the flow publishes" in result.output
        assert warehouse["writes"] == []

    def test_the_qualified_name_is_refused_too(self, tmp_path, warehouse, monkeypatch):
        """Spelling it out in full is the same table."""
        monkeypatch.setenv("MOTHERDUCK_TABLE", "rst_test.main.data_use")
        result = run(tmp_path, "--hours", "1", "--table", "rst_test.main.data_use")
        assert result.exit_code != 0
        assert warehouse["writes"] == []

    def test_a_different_table_in_the_same_database_is_allowed(
        self, tmp_path, warehouse, monkeypatch
    ):
        """The guard is about one table, not about the database."""
        monkeypatch.setenv("MOTHERDUCK_TABLE", "rst_test.main.data_use")
        result = run(tmp_path, "--hours", "1", "--table", "tracking")
        assert result.exit_code == 0, result.output
        assert len(warehouse["writes"]) == 1


def test_no_database_configured_fails_before_the_first_poll(
    tmp_path, warehouse, monkeypatch
):
    """Say it at the start, not on the first write.

    A round watched for an hour should not discover at minute two that it had
    nowhere to write. `resolve_database` raises before the client is built.
    """
    monkeypatch.delenv("MOTHERDUCK_DATABASE", raising=False)
    result = run(tmp_path, "--hours", "1", "--table", "tracking")
    assert result.exit_code != 0
    assert "MOTHERDUCK_DATABASE" in result.output
    assert warehouse["writes"] == []
