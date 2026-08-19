"""Tests for the bilingual demo flow builder.

The builder exists to stop two language variants of one instrument drifting
apart, so most of these tests are about the two languages agreeing rather than
about either being correct on its own. The rest pin down the limits that fail
silently: an over-long list item, a comma inside a label, an emoji in a string
that gets compared literally.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_data_use_demo as demo  # noqa: E402

from requests_to_twilio.flows import (  # noqa: E402
    check_flow,
    evaluate_condition,
    route_split,
)

LANGS = sorted(demo.LANGS)

#: The publish widget's name for whichever destination is the default.
#:
#: Derived rather than written out, because these tests are about the payload
#: and the routing into it - which are identical for both destinations - and not
#: about which one a build happens to target. Hard-coding a name here made
#: changing the default look like fourteen broken tests.
PUBLISH_WIDGET = demo.PUBLISH_TARGETS[demo.DEFAULT_PUBLISH_TARGET]["widget"]


def fake_sids(lang: str) -> dict[str, str]:
    """Every content SID the flow needs, without touching Twilio."""
    names = [
        demo.LANGS[lang]["intro_template"],
        demo.LANGS[lang]["close_template"],
        demo.consent_template_name(lang),
    ]
    names += [demo.question_template_name(lang, k) for k in demo.templated_keys(lang)]
    return {name: f"HX{index:032d}" for index, name in enumerate(names)}


def fake_functions() -> dict[str, str]:
    """Build the deployed Functions coordinates, without touching Twilio.

    Shaped like `resolve_functions` output. The domain deliberately carries a
    random-looking suffix, because the real one does - that is what made these
    unguessable and is why they are looked up rather than written down.
    """
    host = "rtt-survey-0000-prod.twil.io"
    target = demo.PUBLISH_TARGETS[demo.DEFAULT_PUBLISH_TARGET]
    return {
        "service_sid": f"ZS{0:032d}",
        "environment_sid": f"ZE{0:032d}",
        "encrypt_sid": f"ZH{1:032d}",
        "publish_sid": f"ZH{2:032d}",
        "encrypt_url": f"https://{host}/encrypt-fields",
        # Derived with the widget name, so the URL and the widget cannot
        # disagree about which destination this fixture describes.
        "publish_url": f"https://{host}{target['path']}",
        "publish_widget": target["widget"],
    }


@pytest.fixture(params=LANGS)
def lang(request):
    return request.param


class TestLanguageTables:
    def test_table_is_valid(self, lang):
        assert demo.check_language(lang) == []

    def test_both_languages_ask_the_same_questions(self):
        """Same keys, same option counts. A missing option is a silent bug."""
        shapes = {}
        for language in LANGS:
            table = demo.LANGS[language]
            shapes[language] = {
                key: len(table["arm2"][key]["options"])
                for key in demo.option_keys(language)
            }
            assert sorted(table["arm1"]) == sorted(demo.QUESTION_KEYS)
        assert len(set(map(str, shapes.values()))) == 1, shapes

    def test_option_ids_match_across_languages(self):
        """The id is the analysis key, so it must not be translated."""
        for key in demo.option_keys(LANGS[0]):
            ids = {
                language: [o[0] for o in demo.LANGS[language]["arm2"][key]["options"]]
                for language in LANGS
            }
            assert len(set(map(str, ids.values()))) == 1, (key, ids)

    def test_an_over_long_item_is_reported(self, lang, monkeypatch):
        table = demo.LANGS[lang]
        options = list(table["arm2"]["P1"]["options"])
        options[0] = (options[0][0], "x" * 25, options[0][2])
        monkeypatch.setitem(table["arm2"]["P1"], "options", options)
        assert any("cap is 24" in p for p in demo.check_language(lang))

    def test_a_comma_in_a_label_is_now_harmless(self, lang, monkeypatch):
        """The reason for banning commas was matches_any_of. Regex has none."""
        table = demo.LANGS[lang]
        options = list(table["arm2"]["P1"]["options"])
        options[0] = (options[0][0], "yes, often", options[0][2])
        monkeypatch.setitem(table["arm2"]["P1"], "options", options)
        assert demo.check_language(lang) == []
        pattern = demo.answer_pattern(options)
        assert evaluate_condition("regex", pattern, "yes, often")

    def test_a_label_with_regex_metacharacters_still_matches_itself(
        self, lang, monkeypatch
    ):
        """Escaping is the whole reason this is generated and not hand-written."""
        table = demo.LANGS[lang]
        options = list(table["arm2"]["P1"]["options"])
        options[0] = (options[0][0], "a+b (c) [d]", options[0][2])
        monkeypatch.setitem(table["arm2"]["P1"], "options", options)
        pattern = demo.answer_pattern(options)
        assert evaluate_condition("regex", pattern, "a+b (c) [d]")
        assert not evaluate_condition("regex", pattern, "aab c d")

    def test_an_emoji_in_a_label_is_reported(self, lang, monkeypatch):
        """Warmth goes in the body; labels are compared byte for byte."""
        table = demo.LANGS[lang]
        options = list(table["arm2"]["P1"]["options"])
        options[0] = (options[0][0], "0 times \U0001f44d", options[0][2])
        monkeypatch.setitem(table["arm2"]["P1"], "options", options)
        assert any("emoji" in p for p in demo.check_language(lang))

    def test_duplicate_labels_are_reported(self, lang, monkeypatch):
        table = demo.LANGS[lang]
        options = list(table["arm2"]["P1"]["options"])
        options[1] = (options[1][0], options[0][1], options[1][2])
        monkeypatch.setitem(table["arm2"]["P1"], "options", options)
        assert any("repeats" in p for p in demo.check_language(lang))

    def test_accented_letters_are_not_flagged_as_emoji(self):
        """Spanish needs them and they compare fine."""
        assert not demo._has_emoji("Telefónica (CATI)")
        assert not demo._has_emoji("Más de 10 veces")


class TestAnswerPattern:
    """The pattern is executed here, not read.

    An option that cannot be selected looks identical to one that can, right up
    until the data comes back with a column of noMatch.
    """

    def test_the_alternation_is_wrapped(self):
        """Studio anchors the whole pattern, so bare a|b binds as (^a)|(b$)."""
        pattern = demo.answer_pattern([("a", "Yes", "d"), ("b", "No", "d")])
        assert not evaluate_condition("regex", pattern, "say Yes")
        assert not evaluate_condition("regex", pattern, "No thank you")

    def test_every_label_is_accepted(self, lang):
        for key in demo.option_keys(lang):
            options = demo.LANGS[lang]["arm2"][key]["options"]
            pattern = demo.answer_pattern(options)
            for _, item, _ in options:
                assert evaluate_condition("regex", pattern, item), (key, item)

    def test_a_typed_position_is_accepted_where_it_is_unambiguous(self, lang):
        for key in demo.option_keys(lang):
            options = demo.LANGS[lang]["arm2"][key]["options"]
            if demo.positions_are_ambiguous(options):
                continue
            pattern = demo.answer_pattern(options)
            for index in range(1, len(options) + 1):
                assert evaluate_condition("regex", pattern, str(index)), (key, index)

    def test_a_typed_position_is_refused_where_the_labels_are_numbers(self, lang):
        """On a 0/1/2-3 scale, a typed "1" means the label, not the position.

        Accepting it stores the option before the one the respondent meant, with
        the status recording a clean answer. Refusing sends them back to the
        list, which is the right outcome for a reply with two readings.
        """
        # Built here rather than found in the tables. The instrument currently
        # has no numeric labels, so looking for one would make this test pass by
        # having nothing to check - and it guards the mechanism, which has to
        # keep working for the next scale somebody writes.
        options = [
            ("f_0", "0 times", "Not once"),
            ("f_1_2", "1-2 times", "Once or twice"),
            ("f_3_5", "3-5 times", "A handful"),
        ]
        assert demo.positions_are_ambiguous(options)

        pattern = demo.answer_pattern(options)
        for index in range(1, len(options) + 1):
            assert not evaluate_condition("regex", pattern, str(index)), index
        # The labels themselves must still route, or the question is unanswerable.
        for _, item, _ in options:
            assert evaluate_condition("regex", pattern, item)

    def test_no_current_question_has_ambiguous_positions(self, lang):
        """If one appears, the nudge has to change with it.

        `error_body_for` picks the label-only wording for these, so a question
        that became ambiguous without that switch would tell the respondent to
        reply with a number the split is required to refuse.
        """
        for key in demo.option_keys(lang):
            options = demo.LANGS[lang]["arm2"][key]["options"]
            if demo.positions_are_ambiguous(options):
                table = demo.LANGS[lang]
                assert demo.error_body_for(
                    table, options, demo.question_kind(lang, key)
                ) == table["error_option_labels"].format(button=table["arm2"]["button"])

    def test_typed_punctuation_and_casing_are_tolerated(self, lang):
        for key in demo.option_keys(lang):
            options = demo.LANGS[lang]["arm2"][key]["options"]
            pattern = demo.answer_pattern(options)
            if not demo.positions_are_ambiguous(options):
                for reply in ("1.", "1)", "(1)", " 1 "):
                    assert evaluate_condition("regex", pattern, reply), (key, reply)
            label = options[0][1]
            assert evaluate_condition("regex", pattern, label.upper())
            assert evaluate_condition("regex", pattern, f" {label} ")

    def test_junk_is_rejected(self, lang):
        """A pattern that accepts anything is worse than one that accepts too little."""
        for key in demo.option_keys(lang):
            options = demo.LANGS[lang]["arm2"][key]["options"]
            pattern = demo.answer_pattern(options)
            for junk in ("banana", "", "0", "99", "yes please", "times"):
                assert not evaluate_condition("regex", pattern, junk), (key, junk)


class TestCodeMapping:
    def test_tapping_and_typing_produce_the_same_code(self, lang):
        """The stored value must not depend on how the respondent answered."""
        for key in demo.option_keys(lang):
            options = demo.LANGS[lang]["arm2"][key]["options"]
            positional = not demo.positions_are_ambiguous(options)
            for index, (_, item, _) in enumerate(options, start=1):
                assert demo.expected_code(options, item) == str(index)
                if positional:
                    assert demo.expected_code(options, str(index)) == str(index)

    def test_an_ambiguous_digit_codes_as_other_rather_than_guessing(self, lang):
        """The mapping must refuse the same replies the split refuses.

        If the mapping were the more tolerant of the two it would put a code on
        a reply the split had already sent back to the retry - two records of
        the same respondent disagreeing about whether they answered.
        """
        for key in demo.option_keys(lang):
            options = demo.LANGS[lang]["arm2"][key]["options"]
            if not demo.positions_are_ambiguous(options):
                continue
            for index in range(1, len(options) + 1):
                assert demo.expected_code(options, str(index)) == "other", (key, index)

    def test_the_split_and_the_mapping_agree(self, lang):
        """Anything accepted as an answer must code as one, or the row lies."""
        for key in demo.option_keys(lang):
            options = demo.LANGS[lang]["arm2"][key]["options"]
            pattern = demo.answer_pattern(options)
            for index, (_, item, _) in enumerate(options, start=1):
                for reply in (
                    item,
                    item.upper(),
                    str(index),
                    f"{index}.",
                    f"({index})",
                ):
                    if evaluate_condition("regex", pattern, reply):
                        assert demo.expected_code(options, reply) == str(index), reply

    def test_unrecognised_input_is_not_coded_as_an_answer(self):
        options = [("a", "Yes", "d")]
        assert demo.expected_code(options, "banana") == "other"
        assert "{% else %}other{% endcase %}" in demo.code_mapping("q", options)

    def test_an_option_may_declare_a_code_outside_the_scale(self):
        """A "Prefer not to say" row must not become a 6 on a 5-point item.

        Left to its position it would be averaged in as if it were the top of
        the scale, which is the kind of error that survives into a published
        mean.
        """
        likert = [
            ("r1", "1 - Very dissatisfied", "d"),
            ("r2", "2 - Dissatisfied", "d"),
            ("r3", "3 - Neither", "d"),
            ("r4", "4 - Satisfied", "d"),
            ("r5", "5 - Very satisfied", "d"),
            ("rna", "Prefer not to say", "d", -99),
        ]
        assert demo.expected_code(likert, "5 - Very satisfied") == "5"
        assert demo.expected_code(likert, "Prefer not to say") == "-99"
        assert demo.expected_code(likert, "6") == "-99"
        assert demo.expected_code(likert, "rna") == "-99"
        assert '{% when "rna" or "prefer not to say" or "6" %}-99' in demo.code_mapping(
            "q", likert
        )

    def test_a_scale_point_still_codes_by_position(self):
        likert = [("r1", "1 - Low", "d"), ("r2", "2 - High", "d")]
        assert demo.option_code(likert[0], 1) == "1"
        assert demo.option_code(("x", "y", "z", -99), 6) == "-99"

    def test_the_liquid_normalises_before_comparing(self):
        """Otherwise the tolerant split and the strict mapping disagree."""
        liquid = demo.code_mapping("q", [("opt_a", "Yes", "d")])
        assert "| strip | downcase" in liquid
        assert '| replace: ".", ""' in liquid
        # id first: a tapped list row sends the id, not the label.
        assert '{% when "opt_a" or "yes" or "1" %}1' in liquid


class TestTemplateDefinitions:
    def test_every_template_the_flow_needs_is_emitted(self, lang):
        emitted = set(demo.template_definitions(lang))
        expected = {demo.consent_template_name(lang)} | {
            demo.question_template_name(lang, k) for k in demo.templated_keys(lang)
        }
        assert emitted == expected

    def test_questions_are_list_pickers_with_a_text_fallback(self, lang):
        definition = demo.question_definition(lang, "P1")
        assert set(definition["types"]) == {"twilio/text", "twilio/list-picker"}
        assert definition["types"]["twilio/list-picker"]["items"]

    def test_consent_is_quick_reply_with_two_buttons(self, lang):
        definition = demo.consent_definition(lang)
        actions = definition["types"]["twilio/quick-reply"]["actions"]
        assert len(actions) == 2
        assert [a["id"] for a in actions] == ["consent_yes", "consent_no"]

    def test_the_text_fallback_numbers_the_options_in_order(self, lang):
        """The digits only exist in the fallback, and must line up with them."""
        definition = demo.question_definition(lang, "P1")
        fallback = definition["types"]["twilio/text"]["body"]
        items = definition["types"]["twilio/list-picker"]["items"]
        for index, item in enumerate(items, start=1):
            assert f"{index} - {item['item']}" in fallback

    def test_language_is_declared(self, lang):
        assert demo.consent_definition(lang)["language"] == demo.LANGS[lang]["language"]

    def test_a_button_question_is_quick_reply_not_a_list(self, lang):
        keys = [
            k for k in demo.QUESTION_KEYS if demo.question_kind(lang, k) == "button"
        ]
        assert keys, "expected at least one quick-reply question"
        for key in keys:
            types = demo.question_definition(lang, key)["types"]
            assert set(types) == {"twilio/text", "twilio/quick-reply"}
            actions = types["twilio/quick-reply"]["actions"]
            options = demo.LANGS[lang]["arm2"][key]["options"]
            # The id is what a tap sends, so it has to be the option's own id and
            # not a positional one - that was the first live test's failure.
            assert [a["id"] for a in actions] == [o[0] for o in options]
            assert [a["title"] for a in actions] == [o[1] for o in options]

    def test_an_integer_question_has_no_template_at_all(self, lang):
        """Not an empty one - none. There is nothing for Twilio to render."""
        for key in demo.QUESTION_KEYS:
            if demo.question_kind(lang, key) != "integer":
                continue
            assert key not in demo.templated_keys(lang)
            name = demo.question_template_name(lang, key)
            assert name not in demo.template_definitions(lang)


class TestBuild:
    def test_both_languages_pass_every_flow_check(self, lang):
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        assert check_flow(definition) == []

    def test_both_languages_have_identical_structure(self):
        """Only strings may differ. Same widgets, same wiring, same order."""
        graphs = {}
        for language in LANGS:
            definition = demo.build(language, fake_sids(language), fake_functions())
            graphs[language] = [
                (
                    state["name"],
                    state["type"],
                    [(t.get("event"), t.get("next")) for t in state["transitions"]],
                )
                for state in definition["states"]
            ]
        first, *rest = graphs.values()
        for other in rest:
            assert other == first

    def test_a_missing_content_sid_refuses_to_build(self, lang):
        sids = fake_sids(lang)
        sids.pop(demo.consent_template_name(lang))
        with pytest.raises(demo.BuildError, match="missing content SIDs"):
            demo.build(lang, sids, fake_functions())

    def test_an_unknown_language_refuses_to_build(self):
        with pytest.raises(demo.BuildError, match="unknown language"):
            demo.build("fr", {}, fake_functions())

    def test_the_opener_is_the_only_widget_needing_approval(self, lang):
        """Everything else is in session, which is why this is cheap."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        from requests_to_twilio.flows import opening_sends

        assert opening_sends(definition) == ["intro"]

    def test_every_arm2_answer_publishes_a_raw_value_and_a_code(self, lang):
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        publish = next(s for s in definition["states"] if s["name"] == PUBLISH_WIDGET)
        keys = [p["key"] for p in publish["properties"]["parameters"]]
        for key in demo.QUESTION_KEYS:
            assert f"ARM2_{key}" in keys
            assert f"ARM2_{key}_status" in keys
            # The raw reply is a label when tapped and a digit when typed; the
            # derived column is what an analyst should use. Both are kept: the
            # derived one can fail, and then the answer is still there.
            #
            # An integer question has no options to code against, so it derives
            # a validated number instead - a different name on purpose, so a
            # free number is never read as an option code.
            derived = "value" if demo.question_kind(lang, key) == "integer" else "code"
            assert f"ARM2_{key}_{derived}" in keys

    def test_arm1_publishes_no_code(self, lang):
        """There is nothing to normalise in an open answer."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        publish = next(s for s in definition["states"] if s["name"] == PUBLISH_WIDGET)
        keys = [p["key"] for p in publish["properties"]["parameters"]]
        assert not any(k.startswith("ARM1_") and k.endswith("_code") for k in keys)

    def test_arm1_asks_every_question_and_validates_none_of_them(self, lang):
        """The arm's defining property, and the easiest one to erode.

        ARM 1 exists to be the uncontrolled comparison: whatever the respondent
        types is stored verbatim, and nothing is re-asked. Adding a split here
        would quietly turn it into ARM 2 and destroy the contrast the whole demo
        is built to show - and it would look like an improvement in review.

        Measured on the first live round: ARM 1 returned "Thursfay", "NO",
        "R and surveycto" and "G00d" where ARM 2 returned 4, 1, 1 and 3.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        for key in demo.QUESTION_KEYS:
            ask = states[f"ARM1_{key}"]
            assert ask["type"] == "send-and-wait-for-reply"
            # A body, never a content template: no options to render.
            assert ask["properties"].get("body")
            assert "content_sid" not in ask["properties"]
            # Three widgets - ask, stop check, store - and no validation split,
            # no retry counter and no give-up branch anywhere in the arm.
            assert f"split_ARM1_{key}" not in states
            assert f"retry_ARM1_{key}" not in states
            assert f"giveup_ARM1_{key}" not in states
            assert f"error_ARM1_{key}" not in states
            assert states[f"store_ARM1_{key}"]["type"] == "set-variables"

    def test_arm1_chains_in_order_and_ends_at_the_finish(self, lang):
        """A question that transitions to the wrong place is a skipped question.

        Both arms run the same keys, so an off-by-one in either chain shows up
        as a systematically missing variable in one arm only - which reads as a
        respondent behaviour difference rather than as a wiring bug.

        The walk resolves through a `confirm_` widget where there is one. A
        confirmation sits between the store and the next question and is not a
        step in the instrument, so a chain that runs through one is still in
        order; a chain that ends at one is not.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        for index, key in enumerate(demo.QUESTION_KEYS):
            store = states[f"store_ARM1_{key}"]
            following = [t.get("next") for t in store["transitions"]]
            # One hop, not a general walk: anything longer than a confirmation
            # between a store and the next question is a wiring bug this test
            # exists to catch, not a shape it should accommodate.
            resolved = []
            for target in following:
                if target and target.startswith("confirm_"):
                    resolved += [t.get("next") for t in states[target]["transitions"]]
                else:
                    resolved.append(target)
            expected = (
                f"ARM1_{demo.QUESTION_KEYS[index + 1]}"
                if index + 1 < len(demo.QUESTION_KEYS)
                else "mark_complete"
            )
            assert expected in resolved, (key, following, resolved)

    def test_p6_slot_mapping_stays_parallel_to_the_code_mapping(self, lang):
        """A second mapping over one question is the thing answers.py warns about.

        The confirmation has to name the slot, and the raw reply is not the
        slot: somebody who types `4` rather than tapping would otherwise be told
        their flight is at 4. So P6 carries a label mapping beside its code
        mapping - two artefacts over one set of options, which is exactly the
        shape that fails silently when they disagree.

        What makes it safe is that both are generated from one options tuple in
        one pass. This pins that property down: identical `when` clauses,
        differing only in what they emit. If someone adds a slot to one and not
        the other, a respondent gets credited with option 7 and told to be ready
        for option 6's flight, and nothing else in the suite would notice.
        """
        options = demo.LANGS[lang]["arm2"]["P6"]["options"]
        pattern = r"\{% when (.+?) %\}([^{]*)"

        code_clauses = re.findall(pattern, demo.code_mapping("ARM2_P6", options))
        slot_clauses = re.findall(pattern, demo.slot_mapping("ARM2_P6", options))

        assert [c for c, _ in code_clauses] == [c for c, _ in slot_clauses]
        assert len(slot_clauses) == len(options)
        # And each branch emits its own option's label, in order.
        assert [v for _, v in slot_clauses] == [option[1] for option in options]

    def test_p6_confirmation_reaches_the_finish_in_both_arms(self, lang):
        """The confirmation is a message, not a step. It must not swallow the flow.

        A send widget that failed to hand back to `mark_complete` would leave a
        respondent who answered every question sitting at a dead end, having
        been thanked for nothing - and `outcome` would never be set, so the row
        would publish as incomplete.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        for arm in ("ARM1", "ARM2"):
            confirm = states[f"confirm_{arm}_P6"]
            targets = {t.get("next") for t in confirm["transitions"]}
            # Both the sent and failed events, so an undelivered confirmation
            # still completes the survey.
            assert targets == {"mark_complete"}, (arm, targets)

    def test_arm1_stop_words_are_honoured_at_every_question(self, lang):
        """Stopping must work in the arm with no other machinery around it."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        for key in demo.QUESTION_KEYS:
            check = states[f"stopcheck_ARM1_{key}"]
            for word in demo.LANGS[lang]["stop_words"]:
                assert route_split(check, word) == "mark_optout", (key, word)
            # And an ordinary answer must not be mistaken for one.
            assert route_split(check, "Thursday") == f"store_ARM1_{key}"

    def test_both_arms_ask_the_same_questions(self, lang):
        """The comparison is only a comparison if the keys line up."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        names = {s["name"] for s in definition["states"]}
        for key in demo.QUESTION_KEYS:
            assert f"ARM1_{key}" in names
            assert f"ARM2_{key}" in names

    def test_the_retry_nudge_names_the_button_that_is_on_screen(self, lang):
        """And only names it where there is one.

        The list button belongs to a list picker. Naming it on a quick-reply
        question sends the respondent looking for a control that is not on their
        screen, and on a typed number there is no control at all.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        table = demo.LANGS[lang]
        button = table["arm2"]["button"]
        states = {s["name"]: s for s in definition["states"]}

        errors = [n for n in states if n.startswith("error_ARM2_")]
        assert errors
        for name in errors:
            key = name.removeprefix("error_ARM2_")
            body = states[name]["properties"]["body"]
            assert "{button}" not in body
            if demo.question_kind(lang, key) == "list":
                assert button in body
            else:
                assert button not in body, (key, body)

    def test_the_numeric_nudge_states_the_range_it_enforces(self, lang):
        """A nudge that says "a number" after refusing 42 is not a nudge.

        The respondent replied with a number, as asked, and was refused. Telling
        them the bound is the difference between a retry they can act on and one
        that reads as the survey being broken.
        """
        for key in demo.QUESTION_KEYS:
            if demo.question_kind(lang, key) != "integer":
                continue
            question = demo.LANGS[lang]["arm2"][key]
            highest = max(question["accepts"], key=int)
            # Both ends named, in the body that asks and the nudge that re-asks.
            assert highest in question["body"]
            assert highest in demo.LANGS[lang]["error_numeric"]

    def test_a_number_outside_the_range_is_refused_in_the_real_flow(self, lang):
        """ARM 2 bounds every answer, and a number is not exempt.

        Built end to end rather than against the constraint in isolation: the
        pattern being right is not the same as the split using it, and a
        question that validated nothing would still look correct in the table.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        for key in demo.QUESTION_KEYS:
            if demo.question_kind(lang, key) != "integer":
                continue
            split = states[f"split_ARM2_{key}"]
            question = demo.LANGS[lang]["arm2"][key]
            for reply in question["accepts"]:
                assert route_split(split, reply) == f"store_ARM2_{key}", (key, reply)
            for reply in question["refuses"]:
                assert route_split(split, reply) == f"retry_ARM2_{key}", (key, reply)

    def test_a_number_question_publishes_a_value_and_never_a_code(self, lang):
        """A free number is not an option code, so it must not be named like one."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        publish = next(s for s in definition["states"] if s["name"] == PUBLISH_WIDGET)
        keys = {p["key"] for p in publish["properties"]["parameters"]}
        for key in demo.QUESTION_KEYS:
            if demo.question_kind(lang, key) != "integer":
                continue
            assert f"ARM2_{key}_value" in keys
            assert f"ARM2_{key}_code" not in keys

    def test_every_option_routes_to_store_in_the_real_flow(self, lang):
        """End to end on the built definition, not on the pattern in isolation.

        This is the check that would have caught the defect the account already
        has: an answer that looks handled but sends the respondent somewhere
        that never publishes.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        for key in demo.option_keys(lang):
            split = states[f"split_ARM2_{key}"]
            options = demo.LANGS[lang]["arm2"][key]["options"]
            ambiguous = demo.positions_are_ambiguous(options)

            for index, (option_id, item, _) in enumerate(options, start=1):
                # The id is what a tapped list row sends; the label is what a
                # tapped quick-reply button sends. Both must land on store.
                replies = [option_id, item, item.upper()]
                if not ambiguous:
                    replies += [str(index), f"{index}."]
                for reply in replies:
                    assert route_split(split, reply) == f"store_ARM2_{key}", (
                        key,
                        reply,
                    )

            if ambiguous:
                # A number on a numerically-labelled scale has two readings, so
                # it goes back to the list rather than being coded as a guess.
                for index in range(1, len(options) + 1):
                    assert route_split(split, str(index)) == f"retry_ARM2_{key}", (
                        key,
                        index,
                    )

            for junk in ("banana", "99"):
                assert route_split(split, junk) == f"retry_ARM2_{key}", (key, junk)

    def test_consent_routes_both_ways_in_the_real_flow(self, lang):
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        split = next(s for s in definition["states"] if s["name"] == "split_consent")
        consent = demo.LANGS[lang]["consent"]

        for reply in [consent["button_yes"], *consent["typed_yes"].split("|")]:
            assert route_split(split, reply) == "record_consent", reply
        for reply in [consent["button_no"], *consent["typed_no"].split("|")]:
            assert route_split(split, reply) == "record_declined", reply

    def test_someone_who_never_replies_gets_a_row_and_a_closing_template(self, lang):
        """They are owed closure, and only a template can reach them.

        Somebody contacted once who did not answer is left not knowing whether
        anything is still expected of them. They never opened the 24-hour
        window, so a free-form close fails with 63016 - the approved template
        is the only mechanism that gets there.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        # Walk the timeout edge out of the opener, evaluating splits properly
        # rather than taking whichever branch happens to be first.
        node, event, path, outcome = "intro", "timeout", [], None
        while node and node not in path:
            path.append(node)
            state = states[node]

            for variable in state["properties"].get("variables", []):
                if variable["key"] == "outcome":
                    outcome = variable["value"]

            if state["type"] == "split-based-on":
                node = route_split(state, outcome or "")
            else:
                following = [
                    t.get("next")
                    for t in state["transitions"]
                    if t.get("event") == event and t.get("next")
                ]
                if not following:
                    following = [
                        t.get("next") for t in state["transitions"] if t.get("next")
                    ]
                node = following[0] if following else None
            event = "next"

        assert outcome == "unreachable", path
        assert PUBLISH_WIDGET in path, path
        assert path[-1] == "close_never_started", path

    def test_the_close_to_a_non_responder_is_a_template_not_a_body(self, lang):
        """A free-form body here fails with 63016 for every single one of them."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        close = next(
            s for s in definition["states"] if s["name"] == "close_never_started"
        )
        assert close["properties"]["message_type"] == "content_template"
        assert close["properties"]["content_sid"]
        assert "body" not in close["properties"]

    def test_the_in_session_closes_stay_free_form(self, lang):
        """They have an inbound reply within the hour, so nothing is frozen.

        That matters: it lets each carry its own longer, outcome-specific text,
        including the reveal of the experiment.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}
        for name in ("close_complete", "close_declined", "close_incomplete"):
            assert states[name]["properties"]["body"]
            assert "content_sid" not in states[name]["properties"]

    def test_every_outcome_is_routed_deliberately(self, lang):
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        split = next(s for s in definition["states"] if s["name"] == "split_closing")
        for outcome, expected in (
            ("complete", "close_complete"),
            ("declined", "close_declined"),
            ("incomplete", "close_incomplete"),
            ("unreachable", "close_never_started"),
            # The one case nothing can reach: the first message never arrived,
            # so neither would this one.
            ("undeliverable", "end_without_message"),
        ):
            assert route_split(split, outcome) == expected, outcome

    def test_both_bookend_templates_are_required_to_build(self, lang):
        """Missing either one must fail loudly, not silently ship a broken flow."""
        for key in ("intro_template", "close_template"):
            sids = fake_sids(lang)
            sids.pop(demo.LANGS[lang][key])
            with pytest.raises(demo.BuildError, match="missing content SIDs"):
                demo.build(lang, sids, fake_functions())

    def test_an_unreadable_consent_reply_does_not_enrol_anyone(self, lang):
        """Ambiguity must never be read as agreement."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}
        split = states["split_consent"]
        for reply in ("maybe", "what is this", ""):
            assert route_split(split, reply) != "record_consent", reply

    def test_an_unreadable_consent_reply_is_not_a_refusal_either(self, lang):
        """It is a parse failure, and refusal rate is a headline number.

        Routing it to `record_declined` published "what is this?", a voice note
        and an emoji as explicit declines. The respondent gets one re-ask, and
        if that is unreadable too the row says `unclear`, not `no`.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        assert route_split(states["split_consent"], "maybe") == "consent_unclear"
        assert states["consent_unclear"]["properties"]["variables"] == [
            {"key": "set_consent", "value": "unclear"}
        ]

        # The re-ask still accepts a real answer either way.
        retry = states["split_consent_retry"]
        yes = demo.LANGS[lang]["consent"]["button_yes"]
        no = demo.LANGS[lang]["consent"]["button_no"]
        assert route_split(retry, yes) == "record_consent"
        assert route_split(retry, no) == "record_declined"
        # A second unreadable reply is a break-off, not a decision.
        assert route_split(retry, "still confused") == "mark_no_reply"

    def test_stop_is_honoured_at_every_question_in_both_arms(self, lang):
        """Saying stop and then being asked three more questions is an ethics
        problem before it is a bug.

        Twilio's own opt-out handling covers the carrier keywords for SMS. In a
        WhatsApp session a "STOP" is an ordinary inbound message: in ARM 1 it
        was stored verbatim as the answer, and in ARM 2 it failed the match,
        nudged twice, and then asked the next question anyway.
        """
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        for arm in ("ARM1", "ARM2"):
            for key in demo.QUESTION_KEYS:
                check = states[f"stopcheck_{arm}_{key}"]
                for word in demo.LANGS[lang]["stop_words"]:
                    assert route_split(check, word) == "mark_optout", (arm, key, word)
                    assert route_split(check, word.upper()) == "mark_optout"

    def test_stopping_is_a_distinct_outcome_not_a_break_off(self, lang):
        """Their answers so far are kept, and the blanks are explained."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        variables = {
            v["key"]: v["value"]
            for v in states["mark_optout"]["properties"]["variables"]
        }
        assert variables["outcome"] == "optout"

        # It still converges on the publish path, so a row exists.
        assert states["mark_optout"]["transitions"][0]["next"] == "finish"

        # And it is acknowledged: silence after "stop" is indistinguishable
        # from not having been heard.
        assert route_split(states["split_closing"], "optout") == "close_optout"

    def test_a_missing_arm_is_visible_in_the_data(self, lang):
        """A misspelled `arm` column would otherwise become a silent one-arm study."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        states = {s["name"]: s for s in definition["states"]}

        assert route_split(states["split_arm"], "") == "mark_arm_missing"
        assert route_split(states["split_arm"], "1") == "ARM1_P1"
        assert route_split(states["split_arm"], "2") == "ARM2_P1"

        published = {
            p["key"] for p in states[PUBLISH_WIDGET]["properties"]["parameters"]
        }
        assert "set_arm_missing" in published

    def test_the_flow_records_which_language_it_was(self, lang):
        """Two flows write to one table; the rows have to be separable."""
        definition = demo.build(lang, fake_sids(lang), fake_functions())
        publish = next(s for s in definition["states"] if s["name"] == PUBLISH_WIDGET)
        params = {p["key"]: p["value"] for p in publish["properties"]["parameters"]}
        assert params["lang"] == lang


class TestTheBuilderStillEmitsTheCommittedFlow:
    """The regression gate for every future change to the builder.

    `flows/data_use_demo_en.json` is what a round actually runs. It is also the
    only artifact anyone reviews - nobody reads a 73-widget canvas - so a
    refactor that quietly changes one widget is a change to the instrument that
    no reviewer would catch.

    The content SIDs and Functions coordinates are recovered from the committed
    flow itself rather than stubbed, which is what makes the comparison exact
    instead of merely structural: those are the only per-account values in the
    file, so supplying them back means everything else has to match on its own.

    This is deliberately a *pinning* test. If the flow is meant to change, the
    committed JSON is rebuilt in the same commit and the diff is the review.
    """

    def _coordinates(self, committed):
        """Recover the per-account values from the committed definition."""
        sids, functions = {}, {}
        for state in committed["states"]:
            properties = state.get("properties", {})
            if properties.get("content_sid"):
                sids[state["name"]] = properties["content_sid"]
            if state.get("type") == "run-function":
                functions.setdefault("service_sid", properties["service_sid"])
                functions.setdefault("environment_sid", properties["environment_sid"])
                which = "encrypt" if "encrypt" in state["name"] else "publish"
                functions[f"{which}_sid"] = properties["function_sid"]
                functions[f"{which}_url"] = properties["url"]
                if which == "publish":
                    # Which destination the committed flow was built against.
                    # Recovered rather than assumed: the same graph is built for
                    # MotherDuck and for Google Sheets, and they differ only in
                    # this widget's name and URL. Defaulting here instead would
                    # make the test pass only while the committed flow happened
                    # to use the default target.
                    functions["publish_widget"] = state["name"]
        return sids, functions

    @pytest.mark.parametrize("language", LANGS)
    def test_rebuilding_reproduces_the_committed_definition(self, language):
        # Both languages, because only English was pinned and Spanish drifted
        # for it. Editing the ES table left flows/data_use_demo_es.json carrying
        # the previous questions, with a full green suite: the committed flow
        # said one thing, the table it is generated from said another, and
        # nothing compared them.
        table = demo.LANGS[language]
        path = (
            Path(__file__).resolve().parents[1]
            / "flows"
            / f"data_use_demo_{table['flow_suffix']}.json"
        )
        committed = json.loads(path.read_text(encoding="utf-8"))
        sids, functions = self._coordinates(committed)

        by_name = {
            table["intro_template"]: sids["intro"],
            table["close_template"]: sids["close_never_started"],
            demo.consent_template_name(language): sids["consent"],
        }
        for key in demo.templated_keys(language):
            by_name[demo.question_template_name(language, key)] = sids[f"ARM2_{key}"]

        assert demo.build(language, by_name, functions) == committed
