"""Tests for the bilingual demo flow builder.

The builder exists to stop two language variants of one instrument drifting
apart, so most of these tests are about the two languages agreeing rather than
about either being correct on its own. The rest pin down the limits that fail
silently: an over-long list item, a comma inside a label, an emoji in a string
that gets compared literally.
"""

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


def fake_sids(lang: str) -> dict[str, str]:
    """Every content SID the flow needs, without touching Twilio."""
    names = [
        demo.LANGS[lang]["intro_template"],
        demo.LANGS[lang]["close_template"],
        demo.consent_template_name(lang),
    ]
    names += [demo.question_template_name(lang, k) for k in demo.QUESTION_KEYS]
    return {name: f"HX{index:032d}" for index, name in enumerate(names)}


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
                key: len(table["arm2"][key]["options"]) for key in demo.QUESTION_KEYS
            }
            assert sorted(table["arm1"]) == sorted(demo.QUESTION_KEYS)
        assert len(set(map(str, shapes.values()))) == 1, shapes

    def test_option_ids_match_across_languages(self):
        """The id is the analysis key, so it must not be translated."""
        for key in demo.QUESTION_KEYS:
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

    def test_every_label_and_position_is_accepted(self, lang):
        for key in demo.QUESTION_KEYS:
            options = demo.LANGS[lang]["arm2"][key]["options"]
            pattern = demo.answer_pattern(options)
            for index, (_, item, _) in enumerate(options, start=1):
                assert evaluate_condition("regex", pattern, item), (key, item)
                assert evaluate_condition("regex", pattern, str(index)), (key, index)

    def test_typed_punctuation_and_casing_are_tolerated(self, lang):
        for key in demo.QUESTION_KEYS:
            options = demo.LANGS[lang]["arm2"][key]["options"]
            pattern = demo.answer_pattern(options)
            for reply in ("1.", "1)", "(1)", " 1 "):
                assert evaluate_condition("regex", pattern, reply), (key, reply)
            label = options[0][1]
            assert evaluate_condition("regex", pattern, label.upper())

    def test_junk_is_rejected(self, lang):
        """A pattern that accepts anything is worse than one that accepts too little."""
        for key in demo.QUESTION_KEYS:
            options = demo.LANGS[lang]["arm2"][key]["options"]
            pattern = demo.answer_pattern(options)
            for junk in ("banana", "", "0", "99", "yes please", "times"):
                assert not evaluate_condition("regex", pattern, junk), (key, junk)


class TestCodeMapping:
    def test_tapping_and_typing_produce_the_same_code(self, lang):
        """The stored value must not depend on how the respondent answered."""
        for key in demo.QUESTION_KEYS:
            options = demo.LANGS[lang]["arm2"][key]["options"]
            for index, (_, item, _) in enumerate(options, start=1):
                assert demo.expected_code(options, item) == str(index)
                assert demo.expected_code(options, str(index)) == str(index)

    def test_the_split_and_the_mapping_agree(self, lang):
        """Anything accepted as an answer must code as one, or the row lies."""
        for key in demo.QUESTION_KEYS:
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
        assert '{% when "prefer not to say" or "6" %}-99' in demo.code_mapping(
            "q", likert
        )

    def test_a_scale_point_still_codes_by_position(self):
        likert = [("r1", "1 - Low", "d"), ("r2", "2 - High", "d")]
        assert demo.option_code(likert[0], 1) == "1"
        assert demo.option_code(("x", "y", "z", -99), 6) == "-99"

    def test_the_liquid_normalises_before_comparing(self):
        """Otherwise the tolerant split and the strict mapping disagree."""
        liquid = demo.code_mapping("q", [("a", "Yes", "d")])
        assert "| strip | downcase" in liquid
        assert '| replace: ".", ""' in liquid
        assert '{% when "yes" or "1" %}1' in liquid


class TestTemplateDefinitions:
    def test_every_template_the_flow_needs_is_emitted(self, lang):
        emitted = set(demo.template_definitions(lang))
        expected = {demo.consent_template_name(lang)} | {
            demo.question_template_name(lang, k) for k in demo.QUESTION_KEYS
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


class TestBuild:
    def test_both_languages_pass_every_flow_check(self, lang):
        definition = demo.build(lang, fake_sids(lang))
        assert check_flow(definition) == []

    def test_both_languages_have_identical_structure(self):
        """Only strings may differ. Same widgets, same wiring, same order."""
        graphs = {}
        for language in LANGS:
            definition = demo.build(language, fake_sids(language))
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
            demo.build(lang, sids)

    def test_an_unknown_language_refuses_to_build(self):
        with pytest.raises(demo.BuildError, match="unknown language"):
            demo.build("fr", {})

    def test_the_opener_is_the_only_widget_needing_approval(self, lang):
        """Everything else is in session, which is why this is cheap."""
        definition = demo.build(lang, fake_sids(lang))
        from requests_to_twilio.flows import opening_sends

        assert opening_sends(definition) == ["intro"]

    def test_every_arm2_answer_publishes_a_raw_value_and_a_code(self, lang):
        definition = demo.build(lang, fake_sids(lang))
        publish = next(
            s for s in definition["states"] if s["name"] == "publish_motherduck"
        )
        keys = [p["key"] for p in publish["properties"]["parameters"]]
        for key in demo.QUESTION_KEYS:
            assert f"ARM2_{key}" in keys
            assert f"ARM2_{key}_status" in keys
            # The raw reply is a label when tapped and a digit when typed; the
            # code is what an analyst should use. Both are kept: the code is
            # derived, so if the Liquid ever fails the answer is still there.
            assert f"ARM2_{key}_code" in keys

    def test_arm1_publishes_no_code(self, lang):
        """There is nothing to normalise in an open answer."""
        definition = demo.build(lang, fake_sids(lang))
        publish = next(
            s for s in definition["states"] if s["name"] == "publish_motherduck"
        )
        keys = [p["key"] for p in publish["properties"]["parameters"]]
        assert not any(k.startswith("ARM1_") and k.endswith("_code") for k in keys)

    def test_the_retry_nudge_names_the_button_that_is_on_screen(self, lang):
        definition = demo.build(lang, fake_sids(lang))
        button = demo.LANGS[lang]["arm2"]["button"]
        errors = [
            s for s in definition["states"] if s["name"].startswith("error_ARM2_")
        ]
        assert errors
        for state in errors:
            assert button in state["properties"]["body"]
            assert "{button}" not in state["properties"]["body"]

    def test_every_option_routes_to_store_in_the_real_flow(self, lang):
        """End to end on the built definition, not on the pattern in isolation.

        This is the check that would have caught the defect the account already
        has: an answer that looks handled but sends the respondent somewhere
        that never publishes.
        """
        definition = demo.build(lang, fake_sids(lang))
        states = {s["name"]: s for s in definition["states"]}

        for key in demo.QUESTION_KEYS:
            split = states[f"split_ARM2_{key}"]
            options = demo.LANGS[lang]["arm2"][key]["options"]
            for index, (_, item, _) in enumerate(options, start=1):
                for reply in (item, str(index), f"{index}."):
                    assert route_split(split, reply) == f"store_ARM2_{key}", (
                        key,
                        reply,
                    )
            for junk in ("banana", "0", "99"):
                assert route_split(split, junk) == f"retry_ARM2_{key}", (key, junk)

    def test_consent_routes_both_ways_in_the_real_flow(self, lang):
        definition = demo.build(lang, fake_sids(lang))
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
        definition = demo.build(lang, fake_sids(lang))
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
        assert "publish_motherduck" in path, path
        assert path[-1] == "close_never_started", path

    def test_the_close_to_a_non_responder_is_a_template_not_a_body(self, lang):
        """A free-form body here fails with 63016 for every single one of them."""
        definition = demo.build(lang, fake_sids(lang))
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
        definition = demo.build(lang, fake_sids(lang))
        states = {s["name"]: s for s in definition["states"]}
        for name in ("close_complete", "close_declined", "close_incomplete"):
            assert states[name]["properties"]["body"]
            assert "content_sid" not in states[name]["properties"]

    def test_every_outcome_is_routed_deliberately(self, lang):
        definition = demo.build(lang, fake_sids(lang))
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
                demo.build(lang, sids)

    def test_an_unreadable_consent_reply_does_not_enrol_anyone(self, lang):
        """Ambiguity must never be read as agreement."""
        definition = demo.build(lang, fake_sids(lang))
        split = next(s for s in definition["states"] if s["name"] == "split_consent")
        for reply in ("maybe", "what is this", ""):
            assert route_split(split, reply) == "record_declined", reply

    def test_the_flow_records_which_language_it_was(self, lang):
        """Two flows write to one table; the rows have to be separable."""
        definition = demo.build(lang, fake_sids(lang))
        publish = next(
            s for s in definition["states"] if s["name"] == "publish_motherduck"
        )
        params = {p["key"]: p["value"] for p in publish["properties"]["parameters"]}
        assert params["lang"] == lang
