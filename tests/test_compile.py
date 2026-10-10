"""Tests for the survey compiler: spec in, a Studio flow that passes its checks out.

The bar is `check_flow(...) == []` on every instrument the repo ships, in every
language - the same bar the hand-built demo flows are held to. Beyond that, each
thing the compiler decides on the author's behalf is pinned once: where a
condition branches, when the reminder goes out, what is encrypted, what is
published.
"""

from pathlib import Path

import pytest

from requests_to_twilio.compile import (
    compile_spec,
    placeholder_sids,
    required_templates,
    template_definitions,
    template_name,
)
from requests_to_twilio.flows import check_flow, published_columns
from requests_to_twilio.spec import (
    REMINDER_MESSAGE,
    MessageRow,
    load_spec,
    starter_spec,
)
from requests_to_twilio.widgets import BuildError, placeholder_functions

REALISTIC = Path(__file__).resolve().parent / "fixtures" / "realistic_spec.json"

#: Widgets every flow has whatever its questions: the trigger, the outcome
#: markers, finish, encryption and its failure marker, publish and its failure
#: marker, the closing split and the silent ending.
SPINE = {
    "Trigger",
    "mark_complete",
    "mark_no_reply",
    "mark_never_started",
    "mark_delivery_failed",
    "mark_optout",
    "finish",
    "function_encrypt",
    "mark_encrypt_failed",
    "publish_gsheets",
    "record_publish_failure",
    "split_closing",
    "end_without_message",
}


def build(spec, lang="en"):
    return compile_spec(
        spec, lang, placeholder_sids(spec, lang), placeholder_functions()
    )


def states(flow):
    return {s["name"]: s for s in flow["states"]}


def realistic_with_reminders():
    """Load the RST fixture with the starter's reminder, which it predates."""
    spec = load_spec(REALISTIC)
    standard = next(m for m in starter_spec("en").messages if m.key == REMINDER_MESSAGE)
    spec.messages.append(
        MessageRow(key=REMINDER_MESSAGE, text={"en": standard.text["en"], "es": "STOP"})
    )
    return spec


def next_of(state, event):
    return next(t.get("next") for t in state["transitions"] if t["event"] == event)


@pytest.mark.parametrize(
    ("spec", "lang"),
    [
        (starter_spec("en"), "en"),
        (starter_spec("es"), "es"),
        (load_spec(REALISTIC), "en"),
        (load_spec(REALISTIC), "es"),
        (realistic_with_reminders(), "en"),
        (realistic_with_reminders(), "es"),
    ],
    ids=[
        "starter",
        "starter-es",
        "rst-en",
        "rst-es",
        "rst-reminders-en",
        "rst-reminders-es",
    ],
)
def test_every_shipped_instrument_compiles_to_a_flow_with_no_findings(spec, lang):
    assert check_flow(build(spec, lang)) == []


@pytest.mark.parametrize(
    "spec", [starter_spec("en"), load_spec(REALISTIC), realistic_with_reminders()]
)
def test_widget_count_is_what_the_compiler_emits(spec):
    """The workbook's widgets column is a claim; this keeps it true."""
    flow = build(spec)
    rows = sum(spec.widget_count(row) for row in spec.survey)
    spine = len(SPINE & set(states(flow)))
    # The unsolicited reply is a row's widget, so it is already in `rows`.
    assert rows + spine == len(flow["states"])


class TestConditions:
    def test_each_arm_is_its_own_split_on_the_preload(self):
        flow = states(build(load_spec(REALISTIC)))
        arm1 = flow["if_ARM1"]
        condition = arm1["transitions"][1]["conditions"][0]
        assert condition["arguments"] == ["{{flow.data.arm}}"]
        assert (condition["type"], condition["value"]) == ("equal_to", "1")
        assert arm1["transitions"][1]["next"] == "ARM1_P1"
        # Nobody in ARM1 skips to the end: they reach ARM2's split, which lets
        # them through. A missing arm skips both, rather than defaulting to one.
        assert next_of(arm1, "noMatch") == "if_ARM2"
        assert next_of(flow["if_ARM2"], "noMatch") == "mark_complete"

    def test_a_question_can_depend_on_an_earlier_answer(self):
        spec = starter_spec("en")
        spec.row("Q2").relevance = "${Q1}!='1'"
        flow = states(build(spec))
        split = flow["if_Q2"]
        condition = split["transitions"][1]["conditions"][0]
        assert condition["arguments"] == ["{{flow.variables.Q1_code}}"]
        assert (condition["type"], condition["value"]) == ("not_equal_to", "1")
        assert next_of(split, "noMatch") == "if_Q3" or next_of(split, "noMatch") == "Q3"

    def test_the_preload_a_condition_reads_is_published(self):
        columns = [key for key, _ in published_columns(build(load_spec(REALISTIC)))]
        assert "arm" in columns

    def test_a_condition_the_compiler_cannot_read_is_refused(self):
        spec = starter_spec("en")
        spec.row("Q2").relevance = "selected(${Q1}, '1')"
        with pytest.raises(BuildError, match="cannot be compiled"):
            build(spec)


class TestReminders:
    def test_the_question_waits_until_the_reminder_is_due(self):
        spec = starter_spec("en")
        flow = states(build(spec))
        q1 = flow["Q1"]
        assert q1["properties"]["timeout"] == str(spec.settings.reminder_after)
        assert next_of(q1, "timeout") == "remind_check_Q1"

    def test_the_reminder_goes_out_once_then_the_question_times_out(self):
        spec = starter_spec("en")
        flow = states(build(spec))
        assert flow["remind_Q1"]["properties"]["body"] == spec.message(
            REMINDER_MESSAGE, "en"
        )
        again = flow["Q1_again"]
        assert again["properties"]["timeout"] == str(spec.timeout_for(spec.row("Q1")))
        assert next_of(again, "timeout") == "mark_no_reply"
        # A nudged question comes back to Q1; the flag stops a second reminder.
        check = flow["remind_check_Q1"]
        assert check["transitions"][1]["next"] == "mark_no_reply"

    def test_both_replies_are_validated_the_same_way(self):
        flow = states(build(starter_spec("en")))
        assert next_of(flow["got_Q1"], "next") == "split_Q1"
        assert next_of(flow["got_Q1_again"], "next") == "split_Q1"
        assert flow["split_Q1"]["properties"]["input"] == "{{flow.variables.reply_Q1}}"
        assert (
            "flow.variables.reply_Q1"
            in flow["store_Q1"]["properties"]["variables"][1]["value"]
        )

    def test_a_stop_in_reply_to_the_reminder_ends_the_survey(self):
        flow = states(build(starter_spec("en")))
        assert next_of(flow["Q2_again"], "incomingMessage") == "stopcheck_Q2_again"
        assert flow["stopcheck_Q2_again"]["transitions"][1]["next"] == "mark_optout"

    def test_switching_the_reminder_off_waits_the_normal_time(self):
        spec = starter_spec("en")
        spec.row("Q2").timeout_reminder = False
        flow = states(build(spec))
        assert "remind_Q2" not in flow
        assert flow["Q2"]["properties"]["timeout"] == str(
            spec.timeout_for(spec.row("Q2"))
        )
        assert next_of(flow["Q2"], "timeout") == "mark_no_reply"

    def test_the_reminder_is_recorded(self):
        columns = [key for key, _ in published_columns(build(starter_spec("en")))]
        assert "Q1_reminded" in columns


class TestStopWords:
    def test_mid_survey_answers_are_not_checked(self):
        flow = states(build(starter_spec("en")))
        # Q1 is the first question, so part of the opening; Q2 is not.
        assert "stopcheck_Q1" in flow
        assert "stopcheck_Q2" not in flow

    def test_never_means_no_stop_split_anywhere(self):
        spec = starter_spec("en")
        spec.settings.stop_words = "never"
        flow = states(build(spec))
        assert not [name for name in flow if name.startswith("stopcheck_")]
        assert "mark_optout" not in flow

    def test_the_opening_only_skips_the_reminder_reply(self):
        spec = starter_spec("en")
        spec.settings.stop_words = "opening"
        flow = states(build(spec))
        assert "stopcheck_Q1" in flow
        assert "stopcheck_Q2_again" not in flow

    def test_stop_is_understood_in_every_language(self):
        spec = starter_spec("es")
        spec.messages = [m for m in spec.messages if m.key != "stop_words"]
        flow = states(build(spec, "es"))
        pattern = flow["stopcheck_intro"]["transitions"][1]["conditions"][0]["value"]
        assert "stop" in pattern


class TestEncryptionAndPublishing:
    def test_an_encrypted_answer_is_published_only_encrypted(self):
        spec = starter_spec("en")
        spec.row("Q3").encrypt = True
        flow = build(spec)
        published = {
            p["key"]: p["value"]
            for p in states(flow)["publish_gsheets"]["properties"]["parameters"]
        }
        assert "Q3" not in published
        assert published["enc_Q3"] == "{{widgets.function_encrypt.parsed.enc_Q3}}"
        encrypt = states(flow)["function_encrypt"]["properties"]["parameters"]
        assert {"key": "enc_Q3", "value": "{{flow.variables.reply_Q3}}"} in encrypt

    def test_the_phone_number_is_always_encrypted(self):
        params = states(build(starter_spec("en")))["function_encrypt"]["properties"][
            "parameters"
        ]
        assert params[0] == {
            "key": "enc_p_number_original",
            "value": "{{contact.channel.address}}",
        }

    def test_a_name_preload_is_never_published(self):
        """The sample file holds it by caseid; publishing it would copy PII."""
        columns = [key for key, _ in published_columns(build(starter_spec("en")))]
        assert "name" not in columns
        assert "caseid" in columns


class TestTheSpine:
    def test_consent_yes_enrols_and_no_declines(self):
        flow = states(build(starter_spec("en")))
        split = flow["split_consent"]
        assert [t.get("next") for t in split["transitions"][1:]] == [
            "record_consent",
            "record_declined",
        ]
        assert next_of(flow["record_declined"], "next") == "finish"

    def test_the_closing_for_people_who_never_replied_is_a_template(self):
        flow = states(build(starter_spec("en")))
        assert (
            flow["close_unreachable"]["properties"]["message_type"]
            == "content_template"
        )

    def test_a_spec_that_fails_its_checks_is_refused(self):
        spec = starter_spec("en")
        spec.row("Q1").name = "1bad"
        with pytest.raises(BuildError, match="does not pass its checks"):
            build(spec)

    def test_an_unknown_language_is_refused(self):
        with pytest.raises(BuildError, match="not one of this survey's languages"):
            build(starter_spec("en"), "fr")


class TestTemplates:
    def test_a_list_question_becomes_a_list_picker(self):
        spec = starter_spec("en")
        definition = template_definitions(spec, "en")[template_name(spec, "Q1", "en")]
        picker = definition["types"]["twilio/list-picker"]
        assert picker["button"] == spec.message("list_button", "en")
        assert [item["id"] for item in picker["items"]] == [
            c.resolved_id() for c in spec.choice_list("frequency")
        ]
        assert "twilio/text" in definition["types"]

    def test_consent_becomes_quick_reply_buttons(self):
        spec = starter_spec("en")
        definition = template_definitions(spec, "en")[
            template_name(spec, "consent", "en")
        ]
        titles = [
            a["title"] for a in definition["types"]["twilio/quick-reply"]["actions"]
        ]
        assert titles == ["Yes, I will take part", "No, thanks"]

    def test_the_approved_templates_are_required_by_their_own_names(self):
        names = required_templates(starter_spec("en"), "en")
        assert "my_survey_intro" in names
        assert "my_survey_close" in names
