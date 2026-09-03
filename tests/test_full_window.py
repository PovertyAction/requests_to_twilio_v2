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


class TestTheSheetTokenSurvivesTheWindow:
    """A ten-minute token against a sixty-minute watch.

    `TOKEN_LIFETIME_SECONDS` is 600, and `monitor` used to mint once before the
    loop. That was ample while a settled round exited on the first poll, and
    became wrong the moment `--full-window` existed: `just send` runs
    `--hours 1`, so the token expired a sixth of the way in and every later
    write returned 401. `SheetsError` is swallowed on purpose - the CSV is the
    record, a dead sheet must not end the round's monitoring - so the tracking
    tab froze for the remaining fifty minutes while the terminal reported
    "all settled, still watching" and the command exited 0.

    That is the exact shape this repository exists to refuse: the visible
    signals all said healthy and the thing the operator was watching had
    stopped. So the test is about token *count*, not about output.
    """

    @pytest.fixture
    def sheet(self, polls, monkeypatch):
        """Mint dated tokens, and reject any that is over its lifetime."""
        state = {"minted": [], "writes": 0, "rejected": 0}

        monkeypatch.setattr(
            cli,
            "credentials_from_env",
            lambda: ("svc@example.invalid", "-key-", "sheet-id"),
        )

        def fake_access_token(email, key):
            state["minted"].append(polls["now"])
            return f"token@{polls['now']:.0f}"

        def fake_replace_tab(log, *, sheet_id, tab, token):
            age = polls["now"] - float(token.split("@")[1])
            if age > cli.TOKEN_LIFETIME_SECONDS:
                state["rejected"] += 1
                raise cli.SheetsError(
                    "Google Sheets returned HTTP 401: UNAUTHENTICATED"
                )
            state["writes"] += 1
            return len(log), tab

        monkeypatch.setattr(cli, "access_token", fake_access_token)
        monkeypatch.setattr(cli, "replace_tab", fake_replace_tab)
        return state

    def test_every_poll_in_a_full_hour_reaches_the_sheet(self, tmp_path, polls, sheet):
        """Thirty-one polls across the hour, and every one reaches the sheet."""
        result = run(
            tmp_path, "--hours", "1", "--every", "2", "--full-window", "--sheet"
        )

        assert result.exit_code == 0, result.output
        assert polls["polls"] == 31
        assert sheet["writes"] == 31, (
            f"{sheet['rejected']} write(s) hit an expired token; "
            f"the tab froze mid-window"
        )
        assert sheet["rejected"] == 0
        # Minted more than once, and not once per poll either: the refresh is
        # driven by the token's age, so an hour at ten-minute lifetimes needs a
        # handful rather than thirty.
        assert 2 <= len(sheet["minted"]) <= 8, sheet["minted"]
        assert "sheet not updated" not in result.output

    def test_a_dead_sheet_still_does_not_end_the_round(
        self, tmp_path, polls, sheet, monkeypatch
    ):
        """The swallow stays. A sheet that cannot be written is not fatal."""
        monkeypatch.setattr(
            cli,
            "replace_tab",
            lambda *a, **k: (_ for _ in ()).throw(cli.SheetsError("no such tab")),
        )
        result = run(
            tmp_path, "--hours", str(4 / 60), "--every", "2", "--full-window", "--sheet"
        )

        assert result.exit_code == 0, result.output
        assert polls["polls"] == 3
        assert "sheet not updated" in result.output
