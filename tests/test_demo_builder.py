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

from requests_to_twilio.flows import check_flow  # noqa: E402

LANGS = sorted(demo.LANGS)


def fake_sids(lang: str) -> dict[str, str]:
    """Every content SID the flow needs, without touching Twilio."""
    names = [demo.LANGS[lang]["intro_template"], demo.consent_template_name(lang)]
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

    def test_a_comma_in_a_label_is_reported(self, lang, monkeypatch):
        """matches_any_of is comma-delimited, so this never matches."""
        table = demo.LANGS[lang]
        options = list(table["arm2"]["P1"]["options"])
        options[0] = (options[0][0], "yes, often", options[0][2])
        monkeypatch.setitem(table["arm2"]["P1"], "options", options)
        assert any("comma" in p for p in demo.check_language(lang))

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


class TestAcceptedAnswers:
    def test_accepts_both_the_label_and_its_number(self):
        options = [("a", "Yes", "d"), ("b", "No", "d")]
        assert demo.accepted_answers(options) == "Yes,1,No,2"

    def test_every_option_is_reachable(self, lang):
        """An option nobody can select is the defect this repo refuses to ship."""
        for key in demo.QUESTION_KEYS:
            options = demo.LANGS[lang]["arm2"][key]["options"]
            accepted = demo.accepted_answers(options).split(",")
            for index, (_, item, _) in enumerate(options, start=1):
                assert item in accepted
                assert str(index) in accepted


class TestCodeMapping:
    def test_tapping_and_typing_produce_the_same_code(self, lang):
        """The stored value must not depend on how the respondent answered."""
        for key in demo.QUESTION_KEYS:
            options = demo.LANGS[lang]["arm2"][key]["options"]
            liquid = demo.code_mapping(f"ARM2_{key}", options)
            for index, (_, item, _) in enumerate(options, start=1):
                assert f'{{% when "{item}" or "{index}" %}}{index}' in liquid

    def test_unrecognised_input_is_not_coded_as_an_answer(self):
        options = [("a", "Yes", "d")]
        assert "{% else %}other{% endcase %}" in demo.code_mapping("q", options)


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

    def test_the_flow_records_which_language_it_was(self, lang):
        """Two flows write to one table; the rows have to be separable."""
        definition = demo.build(lang, fake_sids(lang))
        publish = next(
            s for s in definition["states"] if s["name"] == "publish_motherduck"
        )
        params = {p["key"]: p["value"] for p in publish["properties"]["parameters"]}
        assert params["lang"] == lang
