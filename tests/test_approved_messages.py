"""Tests for what a survey sends that its author does not type in the card.

The opener and the never-replied closing are approved messages: picked from the
account, with each placeholder mapped to a sample-file column. Any message can
also write ``${column}``. Both end up as Studio filling a value from the sample
file at send time, and both fail the same way when the value is missing - a
blank in the message, or a send Twilio refuses outright - so the checks here
sit as early as each fact is known: the spec, the build, the launch.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from typer.testing import CliRunner

from requests_to_twilio import cli
from requests_to_twilio.compile import (
    compile_spec,
    placeholder_sids,
    selects,
    template_definitions,
    template_name,
)
from requests_to_twilio.flows import check_flow, content_variable_keys
from requests_to_twilio.launcher import LaunchError, launch
from requests_to_twilio.spec import ChoiceRow, check_spec, save_spec, starter_spec
from requests_to_twilio.spec_xlsx import read_xlsx, write_xlsx
from requests_to_twilio.templates import (
    GENERATED_FROM,
    approved_messages,
    content_placeholders,
    load_definition,
)
from requests_to_twilio.widgets import placeholder_functions


def build(spec, lang="en"):
    return compile_spec(
        spec, lang, placeholder_sids(spec, lang), placeholder_functions()
    )


def states(flow):
    return {s["name"]: s for s in flow["states"]}


def row(spec, name):
    return next(r for r in spec.survey if r.name == name)


# -- the mapping ----------------------------------------------------------------


class TestTheMappingFillsThePlaceholders:
    def test_the_opener_fills_each_placeholder_from_its_column(self):
        spec = starter_spec("en")
        spec.settings.preloads.append("district")
        row(spec, "intro").variables = {"1": "name", "2": "district"}
        intro = states(build(spec))["intro"]
        assert intro["properties"]["content_variables"] == [
            {"key": "1", "value": "{{flow.data.name}}"},
            {"key": "2", "value": "{{flow.data.district}}"},
        ]

    def test_the_never_replied_closing_fills_its_own(self):
        spec = starter_spec("en")
        closing = states(build(spec))["close_unreachable"]
        assert closing["properties"]["content_variables"] == [
            {"key": "1", "value": "{{flow.data.name}}"}
        ]

    def test_no_mapping_sends_no_variables(self):
        """Nothing is filled behind the author's back any more."""
        spec = starter_spec("en")
        row(spec, "intro").variables = {}
        assert "content_variables" not in states(build(spec))["intro"]["properties"]

    def test_the_launch_learns_which_columns_must_not_be_blank(self):
        flow = build(starter_spec("en"))
        assert content_variable_keys(flow) == {"name"}


class TestTheSpecChecksTheMapping:
    def test_a_column_that_is_not_a_preload_is_refused(self):
        spec = starter_spec("en")
        row(spec, "intro").variables = {"1": "first_name"}
        assert any("first_name" in p and "preload" in p for p in check_spec(spec))

    def test_a_placeholder_must_be_a_number(self):
        spec = starter_spec("en")
        row(spec, "intro").variables = {"one": "name"}
        assert any("not a placeholder number" in p for p in check_spec(spec))

    def test_a_row_that_sends_no_approved_message_cannot_map_one(self):
        spec = starter_spec("en")
        row(spec, "Q3").variables = {"1": "name"}
        assert any("sends none" in p for p in check_spec(spec))

    def test_the_starter_maps_its_placeholders(self):
        assert check_spec(starter_spec("en")) == []

    def test_the_mapping_survives_the_workbook(self, tmp_path):
        spec = starter_spec("en")
        spec.settings.preloads.append("district")
        row(spec, "intro").variables = {"1": "name", "2": "district"}
        path = tmp_path / "s.xlsx"
        write_xlsx(spec, path)
        assert row(read_xlsx(path), "intro").variables == {"1": "name", "2": "district"}

    def test_the_mapping_is_written_in_placeholder_order(self, tmp_path):
        """The builder writes it in that order whatever it was set in, so Python must."""
        spec = starter_spec("en")
        spec.settings.preloads.append("district")
        row(spec, "intro").variables = {"10": "district", "2": "name", "1": "name"}
        path = tmp_path / "s.json"
        save_spec(spec, path)
        intro = next(
            r
            for r in json.loads(path.read_text("utf-8"))["survey"]
            if r["name"] == "intro"
        )
        assert list(intro["variables"]) == ["1", "2", "10"]


class TestTheBuildRefusesAnUnfilledPlaceholder:
    def test_it_names_the_row_and_the_placeholder(self):
        spec = starter_spec("en")
        existing = SimpleNamespace(
            types={"twilio/quick-reply": {"body": "Hi {{1}}, from {{2}}"}}
        )
        assert cli._unfilled_placeholders(spec, "en", "my_survey_intro", existing) == [
            "intro: {{2}} of my_survey_intro"
        ]

    def test_a_fully_mapped_message_passes(self):
        spec = starter_spec("en")
        existing = SimpleNamespace(types={"twilio/text": {"body": "Hi {{1}}"}})
        assert cli._unfilled_placeholders(spec, "en", "my_survey_intro", existing) == []


class TestTheLaunchRefusesABlankCell:
    def sample(self, tmp_path, names):
        path = tmp_path / "sample.xlsx"
        pd.DataFrame(
            {
                "Number": [f"whatsapp:+1555555010{i}" for i in range(len(names))],
                "caseid": [f"A{i}" for i in range(len(names))],
                "name": names,
            }
        ).to_excel(path, index=False)
        return path

    def test_a_blank_in_a_placeholder_column_stops_the_round(self, tmp_path):
        path = self.sample(tmp_path, ["Ana", None])
        with pytest.raises(LaunchError, match=r"name is blank in row\(s\) 3"):
            launch(
                client=None,
                flow_id="FW" + "0" * 32,
                from_number="whatsapp:+15555550199",
                input_file=path,
                columns_to_send=["caseid", "name"],
                batch_size=10,
                sec_between_batches=0,
                dry_run=True,
                required_columns=frozenset({"name"}),
            )

    def test_a_blank_elsewhere_is_left_to_the_flow(self, tmp_path):
        path = self.sample(tmp_path, ["Ana", None])
        launch(
            client=None,
            flow_id="FW" + "0" * 32,
            from_number="whatsapp:+15555550199",
            input_file=path,
            columns_to_send=["caseid", "name"],
            batch_size=10,
            sec_between_batches=0,
            dry_run=True,
        )


# -- ${column} in a message ---------------------------------------------------------


class TestAColumnInAMessage:
    def test_a_plain_question_reads_the_sample_file(self):
        spec = starter_spec("en")
        row(spec, "Q3").label["en"] = "Thanks ${name}. Anything else?"
        assert states(build(spec))["Q3"]["properties"]["body"].startswith(
            "Thanks {{flow.data.name}}."
        )

    def test_buttons_number_it_and_fill_it_by_variable(self):
        """Twilio content goes by SID, so Studio fills it as a content variable."""
        spec = starter_spec("en")
        row(spec, "Q1").label["en"] = "${name}, how often?"
        widget = next(w for r, w in selects(spec) if r.name == "Q1")
        definition = template_definitions(spec, "en")[template_name(spec, widget, "en")]
        assert definition["types"]["twilio/list-picker"]["body"] == "{{1}}, how often?"
        assert definition["variables"] == {"1": "name"}
        asked = states(build(spec))[widget]["properties"]
        assert asked["content_variables"] == [
            {"key": "1", "value": "{{flow.data.name}}"}
        ]

    def test_a_column_that_is_not_a_preload_is_refused(self):
        spec = starter_spec("en")
        row(spec, "Q3").label["en"] = "Thanks ${surname}."
        assert any("${surname}" in p for p in check_spec(spec))

    def test_the_flow_still_passes_its_checks(self):
        spec = starter_spec("en")
        row(spec, "Q3").label["en"] = "Thanks ${name}."
        assert [f for f in check_flow(build(spec)) if f.severity == "error"] == []


# -- buttons, settings ----------------------------------------------------------------


class TestButtonLabels:
    def test_a_button_label_over_20_is_refused(self):
        spec = starter_spec("en")
        consent = next(c for c in spec.choices if c.value == "yes")
        consent.label["en"] = "Yes, I will take part"
        assert any("WhatsApp shows 20" in p for p in check_spec(spec))

    def test_a_list_option_may_run_to_24(self):
        spec = starter_spec("en")
        ln = row(spec, "Q1").list_name
        spec.choices.append(
            ChoiceRow(list_name=ln, value="9", label={"en": "A label of 22 letters!"})
        )
        assert not any("A label of 22" in p for p in check_spec(spec))


def test_only_the_one_functions_service_is_accepted():
    spec = starter_spec("en")
    spec.settings.functions_service = "my-own-service"
    assert any("functions_service" in p for p in check_spec(spec))


# -- the account's approved messages ---------------------------------------------------


def content_item(name, status, types, language="en", variables=None):
    return SimpleNamespace(
        friendly_name=name,
        language=language,
        approval_requests={"status": status},
        types=types,
        variables=variables or {},
    )


def test_only_approved_messages_are_offered():
    items = [
        content_item(
            "intro",
            "approved",
            {
                "twilio/quick-reply": {
                    "body": "Hi {{1}}",
                    "actions": [{"title": "Start"}],
                }
            },
            variables={"1": "Priya"},
        ),
        content_item("draft", "unsubmitted", {"twilio/text": {"body": "Hi"}}),
    ]
    client = SimpleNamespace(
        content=SimpleNamespace(
            v1=SimpleNamespace(
                content_and_approvals=SimpleNamespace(list=lambda limit: items)
            )
        )
    )
    assert approved_messages(client) == [
        {
            "name": "intro",
            "language": "en",
            "body": "Hi {{1}}",
            "buttons": ["Start"],
            "placeholders": ["1"],
            "samples": {"1": "Priya"},
        }
    ]


def test_placeholders_are_read_from_every_type_in_order():
    types = {
        "twilio/text": {"body": "{{2}} and {{10}}"},
        "twilio/quick-reply": {"body": "{{1}}"},
    }
    assert content_placeholders(types) == ["1", "2", "10"]


# -- one Survey ID, one survey ---------------------------------------------------------


class TestTwoSurveysCannotShareASurveyID:
    def test_another_specs_folder_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        folder = Path("templates") / "generated" / "my_survey"
        folder.mkdir(parents=True)
        (folder / "my_survey_q1_en.json").write_text(
            json.dumps({GENERATED_FROM: "surveys/other.json"}), encoding="utf-8"
        )
        spec_file = tmp_path / "mine.json"
        save_spec(starter_spec("en"), spec_file)
        result = CliRunner().invoke(cli.app, ["survey", "content", str(spec_file)])
        assert result.exit_code == 1
        assert "already belongs to surveys/other.json" in result.output

    def test_the_owner_is_kept_out_of_what_twilio_receives(self, tmp_path):
        path = tmp_path / "x.json"
        path.write_text(
            json.dumps(
                {
                    GENERATED_FROM: "a.json",
                    "friendly_name": "x",
                    "language": "en",
                    "types": {"twilio/text": {"body": "hi"}},
                }
            ),
            encoding="utf-8",
        )
        assert GENERATED_FROM not in load_definition(path)
