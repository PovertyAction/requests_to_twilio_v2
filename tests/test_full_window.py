"""Tests for `rtt monitor --full-window`.

The default is to stop polling once every number has settled, and that is right
for a round being reconciled after the fact. It is wrong for the case this flag
exists for: a `tracking` tab somebody is watching during a live session.

`answered_back` counts as settled, so on a prompt round every number settles a
minute or two in - while the survey itself has barely started - and the default
loop exits with the tab frozen. These tests pin both behaviours, because the
difference is invisible until it happens in front of a room.
"""

import pandas as pd
import pytest
from typer.testing import CliRunner

from requests_to_twilio import cli
from requests_to_twilio.monitor import NUMBER_COLUMNS

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
def polls(monkeypatch):
    """Count polls, and make the clock advance only when the loop sleeps."""
    state = {"polls": 0, "now": 0.0}

    monkeypatch.setattr(cli, "Client", lambda *a, **k: object())
    monkeypatch.setattr(cli, "poll_delivery", lambda **k: pd.DataFrame())

    def fake_by_number(frame, caseids):
        state["polls"] += 1
        return settled_row()

    monkeypatch.setattr(cli, "by_number", fake_by_number)
    monkeypatch.setattr(cli.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(
        cli.time, "sleep", lambda seconds: state.update(now=state["now"] + seconds)
    )
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "0" * 32)
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


def test_the_default_stops_once_everything_has_settled(tmp_path, polls):
    """One poll, then out - the right default for reconciling afterwards."""
    result = run(tmp_path, "--hours", "1", "--every", "2")
    assert result.exit_code == 0, result.output
    assert polls["polls"] == 1
    assert "nothing left to watch" in result.output


def test_full_window_keeps_polling_after_everything_settles(tmp_path, polls):
    """The tab keeps moving until the window is up.

    Ten minutes at two-minute polls is six: the deadline is checked after a
    poll, not before a sleep, so the window closes on a final refresh rather
    than on a two-minute-stale tab. The run has to end by itself at the
    deadline rather than by running out of numbers to watch.
    """
    result = run(tmp_path, "--hours", str(10 / 60), "--every", "2", "--full-window")
    assert result.exit_code == 0, result.output
    assert polls["polls"] == 6
    assert "all settled, still watching" in result.output
    assert "Window closed, every number settled" in result.output


def test_full_window_without_a_window_still_polls_once(tmp_path, polls):
    """Keeping a window open needs a window: no --hours, no loop."""
    result = run(tmp_path, "--full-window")
    assert result.exit_code == 0, result.output
    assert polls["polls"] == 1
