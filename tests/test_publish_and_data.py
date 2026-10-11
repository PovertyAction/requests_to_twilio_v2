"""Tests for what a published row says, and where it goes.

A row has to read the same whatever the person did: a tapped list, a tapped
button and a typed digit are one answer; a question nobody was reminded about
says so; a STOP or a silence shows on the question it happened at. And each
survey's rows go to its own MotherDuck table, named by the flow and checked by
the Function, because a table shared through the Function's environment once
sent a second survey's rows into the first one's table with half its columns
dropped.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from requests_to_twilio import cli, sheets
from requests_to_twilio.answers import label_mapping
from requests_to_twilio.compile import compile_spec, placeholder_sids
from requests_to_twilio.flows import (
    check_flow,
    destination_columns,
    publish_table,
    published_columns,
)
from requests_to_twilio.spec import check_spec, save_spec, starter_spec
from requests_to_twilio.warehouse import table_changes
from requests_to_twilio.widgets import TABLE_PARAMETER, placeholder_functions

REPO_ROOT = Path(__file__).resolve().parent.parent
node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="Node.js is not installed")


def build(spec, target="gsheets"):
    return compile_spec(
        spec, "en", placeholder_sids(spec, "en"), placeholder_functions(target)
    )


def states(flow):
    return {s["name"]: s for s in flow["states"]}


def next_of(state, event):
    return next(t["next"] for t in state["transitions"] if t["event"] == event)


def variables(state):
    return {v["key"]: v["value"] for v in state["properties"]["variables"]}


def parameters(flow):
    publish = next(s for s in flow["states"] if s["name"].startswith("publish_"))
    return [(p["key"], p["value"]) for p in publish["properties"]["parameters"]]


# -- the answer column -------------------------------------------------------------


class TestTheAnswerColumnHoldsTheLabel:
    def test_a_list_question_publishes_its_label_not_the_tap_id(self):
        flow = build(starter_spec("en"))
        assert ("Q1", "{{flow.variables.Q1_answer}}") in parameters(flow)
        store = variables(states(flow)["store_Q1"])
        assert store["Q1_answer"].startswith("{% assign reply =")

    def test_after_the_last_retry_it_keeps_what_was_sent(self):
        give_up = variables(states(build(starter_spec("en")))["giveup_Q1"])
        assert give_up["Q1_answer"] == "{{flow.variables.reply_Q1}}"

    def test_an_open_question_still_publishes_the_reply(self):
        assert ("Q3", "{{flow.variables.reply_Q3}}") in parameters(
            build(starter_spec("en"))
        )

    def test_the_mapping_turns_every_way_of_answering_into_the_label(self):
        options = [("freq_1", "Weekly", ""), ("freq_2", "Monthly", "")]
        liquid = label_mapping("Q1", options)
        # The id a tapped row sends, the title a tapped button sends, a digit.
        assert '{% when "freq_2" or "monthly" or "2" %}Monthly' in liquid


def test_reminded_reads_0_when_no_reminder_went_out():
    assert ("Q1_reminded", "{{flow.variables.Q1_reminded | default: '0'}}") in (
        parameters(build(starter_spec("en")))
    )


class TestTheQuestionSaysWhereItEnded:
    def test_a_stop_marks_the_question_then_ends_the_survey(self):
        flow = states(build(starter_spec("en")))
        assert flow["stopcheck_Q1"]["transitions"][1]["next"] == "optout_Q1"
        assert variables(flow["optout_Q1"]) == {"Q1_status": "optout"}
        assert next_of(flow["optout_Q1"], "next") == "mark_optout"

    def test_silence_marks_the_question_then_ends_the_survey(self):
        flow = states(build(starter_spec("en")))
        assert variables(flow["noreply_Q3"]) == {"Q3_status": "noreply"}
        assert next_of(flow["noreply_Q3"], "next") == "mark_no_reply"

    def test_consent_keeps_its_own_record(self):
        flow = states(build(starter_spec("en")))
        assert flow["stopcheck_consent"]["transitions"][1]["next"] == "mark_optout"
        assert "optout_consent" not in flow

    def test_the_flow_passes_its_checks(self):
        assert check_flow(build(starter_spec("en"))) == []


# -- one table per survey ---------------------------------------------------------------


class TestEachSurveyNamesItsTable:
    def test_a_motherduck_flow_names_its_table_after_the_survey(self):
        flow = build(starter_spec("en"), "motherduck")
        assert parameters(flow)[0] == (TABLE_PARAMETER, "my_survey")
        assert publish_table(flow) == "my_survey"

    def test_the_setting_overrides_the_survey_id(self):
        spec = starter_spec("en")
        spec.settings.publish_table = "peru_2026"
        assert publish_table(build(spec, "motherduck")) == "peru_2026"

    def test_it_is_not_a_column(self):
        flow = build(starter_spec("en"), "motherduck")
        assert TABLE_PARAMETER not in [key for key, _ in published_columns(flow)]

    def test_a_sheets_flow_names_none(self):
        assert publish_table(build(starter_spec("en"))) is None

    def test_a_name_that_is_not_one_identifier_is_refused(self):
        spec = starter_spec("en")
        spec.settings.publish_table = "other_db.main.t"
        assert any("table name" in p for p in check_spec(spec))


def run_node(script):
    # The Function needs `pg` only to connect; these helpers never do.
    stub = (
        "const Module = require('module'); const load = Module._load;"
        "Module._load = function (r, ...a) {"
        "  return r === 'pg' ? { Client: function () {} } : load.call(this, r, ...a); };"
        "const m = require('./twilio_functions/publish_motherduck.js');"
    )
    result = subprocess.run(  # noqa: S603
        [node, "-e", stub + script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ},
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


@requires_node
class TestTheFunctionChecksTheTableItIsGiven:
    def test_a_plain_name_goes_to_the_main_schema(self):
        out = run_node(
            "process.stdout.write(m.resolveTable({}, {rtt_table: 'peru_2026'}))"
        )
        assert out == "main.peru_2026"

    @pytest.mark.parametrize(
        "name", ["db.main.t", "t; DROP TABLE x", 't"', "1table", "a b"]
    )
    def test_anything_else_is_refused(self, name):
        out = run_node(
            "try { m.resolveTable({}, {rtt_table: " + json.dumps(name) + "});"
            " process.stdout.write('ACCEPTED'); }"
            " catch (e) { process.stdout.write('REFUSED'); }"
        )
        assert out == "REFUSED"

    def test_a_flow_from_before_uses_the_configured_table(self):
        out = run_node(
            "process.stdout.write(m.resolveTable({MOTHERDUCK_TABLE: 'db.main.old'}, {}))"
        )
        assert out == "db.main.old"

    def test_the_table_is_never_written_as_a_column(self):
        out = run_node(
            "const q = m.buildInsert('main.t', ['caseid', 'rtt_table'],"
            " {caseid: 'A1', rtt_table: 't'});"
            "process.stdout.write(JSON.stringify(q.used));"
        )
        assert json.loads(out) == ["caseid"]


# -- making the table ------------------------------------------------------------------


class TestTheTableIsMadeToMatchTheFlow:
    def test_a_missing_table_is_created_with_every_column(self):
        assert table_changes(["a", "b"], None) == (True, ["a", "b"], [])

    def test_new_columns_are_added_and_old_ones_kept(self):
        assert table_changes(["a", "c"], ["a", "b"]) == (False, ["c"], ["b"])

    def test_the_command_adds_what_is_missing(self, tmp_path, monkeypatch):
        flow_file = tmp_path / "flow.json"
        flow_file.write_text(
            json.dumps(build(starter_spec("en"), "motherduck")), encoding="utf-8"
        )
        executed = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, statement):
                executed.append(statement)

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return Cursor()

        monkeypatch.setattr(cli.cfg, "load_env", lambda *a, **k: None)
        monkeypatch.setattr(cli, "resolve_database", lambda override: "twilio_train")
        monkeypatch.setattr(cli, "wire_connect", lambda database: Connection())
        monkeypatch.setattr(
            cli, "wire_columns", lambda connection, table: ["caseid", "old_column"]
        )
        result = CliRunner().invoke(cli.app, ["flow", "table", str(flow_file), "--yes"])
        assert result.exit_code == 0, result.output
        assert "twilio_train.main.my_survey" in result.output
        assert "Kept, not sent by this flow: old_column" in result.output
        assert 'ALTER TABLE main.my_survey ADD COLUMN "Q1" VARCHAR' in executed
        assert not any("caseid" in s for s in executed)

    def test_a_sheets_flow_is_refused(self, tmp_path):
        flow_file = tmp_path / "flow.json"
        flow_file.write_text(json.dumps(build(starter_spec("en"))), encoding="utf-8")
        result = CliRunner().invoke(cli.app, ["flow", "table", str(flow_file)])
        assert result.exit_code == 1
        assert "just flow header-sheets" in result.output


# -- writing the sheet's header row ----------------------------------------------------


@pytest.fixture
def sheet(monkeypatch):
    """Stand in for the Sheets API: row 1 holds `row`, writes land in `written`."""
    state = SimpleNamespace(row=[], written=[])
    monkeypatch.setenv("GOOGLE_SHEET_TAB", "data")
    monkeypatch.setattr(cli, "credentials_from_env", lambda: ("sa@x", "key", "SHEET"))
    monkeypatch.setattr(cli, "access_token", lambda email, key: "token")
    monkeypatch.setattr(cli, "read_header", lambda **kw: list(state.row))
    monkeypatch.setattr(
        cli, "write_header", lambda columns, **kw: state.written.append(columns)
    )
    return state


def sheets_flow(tmp_path):
    flow = build(starter_spec("en"))
    flow_file = tmp_path / "flow.json"
    flow_file.write_text(json.dumps(flow), encoding="utf-8")
    return flow, flow_file


class TestTheHeaderRowIsMadeToMatchTheFlow:
    def test_an_empty_row_is_written_with_every_column(self, tmp_path, sheet):
        flow, flow_file = sheets_flow(tmp_path)
        result = CliRunner().invoke(
            cli.app, ["flow", "header", str(flow_file), "--yes"]
        )
        assert result.exit_code == 0, result.output
        assert "Row 1 of tab 'data' is empty" in result.output
        assert sheet.written == [destination_columns(flow)]

    def test_new_columns_go_after_the_last_and_old_ones_are_kept(self, tmp_path, sheet):
        flow, flow_file = sheets_flow(tmp_path)
        sheet.row = ["caseid", "old_column"]
        result = CliRunner().invoke(
            cli.app, ["flow", "header", str(flow_file), "--yes"]
        )
        assert result.exit_code == 0, result.output
        assert "Kept, not sent by this flow: old_column" in result.output
        added = [c for c in destination_columns(flow) if c != "caseid"]
        assert sheet.written == [["caseid", "old_column", *added]]

    def test_a_complete_row_is_left_alone(self, tmp_path, sheet):
        flow, flow_file = sheets_flow(tmp_path)
        sheet.row = destination_columns(flow)
        result = CliRunner().invoke(cli.app, ["flow", "header", str(flow_file)])
        assert result.exit_code == 0, result.output
        assert "already has every column" in result.output
        assert sheet.written == []

    def test_nothing_is_written_without_a_yes(self, tmp_path, sheet):
        _, flow_file = sheets_flow(tmp_path)
        result = CliRunner().invoke(
            cli.app, ["flow", "header", str(flow_file)], input="n\n"
        )
        assert result.exit_code == 1
        assert sheet.written == []

    def test_a_motherduck_flow_is_refused(self, tmp_path, sheet):
        flow_file = tmp_path / "flow.json"
        flow_file.write_text(
            json.dumps(build(starter_spec("en"), "motherduck")), encoding="utf-8"
        )
        result = CliRunner().invoke(cli.app, ["flow", "header", str(flow_file)])
        assert result.exit_code == 1
        assert "publishes to MotherDuck" in result.output

    def test_the_range_is_the_one_the_function_reads(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            sheets, "_request", lambda method, url, token, **kw: calls.append(url) or {}
        )
        assert sheets.read_header(sheet_id="S", tab="Round 1", token="t") == []
        sheets.read_header(sheet_id="S", tab=None, token="t")
        assert calls[0].endswith("/S/values/'Round 1'!A1:FP1")
        assert calls[1].endswith("/S/values/A1:FP1")

    def test_a_header_wider_than_the_function_reads_is_refused(self):
        with pytest.raises(sheets.SheetsError, match="reads only the first 172"):
            sheets.write_header(
                [f"c{i}" for i in range(173)], sheet_id="S", tab="data", token="t"
            )


# -- checking the destination before a send --------------------------------------------


class TestASendChecksTheDestinationFirst:
    def check(self, monkeypatch, flow, *, force=False):
        monkeypatch.setattr(
            cli,
            "resolve_flow",
            lambda client, flow_id: SimpleNamespace(definition=flow),
        )
        cli._check_destination(None, "FW1", force=force)

    def test_an_empty_header_stops_the_send(self, monkeypatch, sheet, capsys):
        with pytest.raises(typer.Exit):
            self.check(monkeypatch, build(starter_spec("en")))
        out = capsys.readouterr().out
        assert "Row 1 of tab 'data' is empty" in out
        assert "just flow header-sheets" in out

    def test_a_dry_run_reports_it_and_carries_on(self, monkeypatch, sheet, capsys):
        self.check(monkeypatch, build(starter_spec("en")), force=True)
        assert "(dry run, continuing)" in capsys.readouterr().out

    def test_a_complete_header_passes(self, monkeypatch, sheet, capsys):
        flow = build(starter_spec("en"))
        sheet.row = destination_columns(flow)
        self.check(monkeypatch, flow)
        assert "destination OK" in capsys.readouterr().out

    def test_a_missing_column_is_named(self, monkeypatch, sheet, capsys):
        flow = build(starter_spec("en"))
        sheet.row = [c for c in destination_columns(flow) if c != "Q1"]
        with pytest.raises(typer.Exit):
            self.check(monkeypatch, flow)
        assert "is missing 1 column(s) the flow sends: Q1" in capsys.readouterr().out

    def test_a_missing_table_stops_the_send(self, monkeypatch, capsys):
        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(cli, "resolve_database", lambda override: "twilio_train")
        monkeypatch.setattr(cli, "wire_connect", lambda database: Connection())
        monkeypatch.setattr(cli, "wire_columns", lambda connection, table: None)
        with pytest.raises(typer.Exit):
            self.check(monkeypatch, build(starter_spec("en"), "motherduck"))
        out = capsys.readouterr().out
        assert "main.my_survey does not exist" in out
        assert "just flow header-motherduck" in out


def test_the_setting_survives_the_json(tmp_path):
    spec = starter_spec("en")
    spec.settings.publish_table = "peru_2026"
    path = tmp_path / "s.json"
    save_spec(spec, path)
    assert (
        json.loads(path.read_text("utf-8"))["settings"]["publish_table"] == "peru_2026"
    )
