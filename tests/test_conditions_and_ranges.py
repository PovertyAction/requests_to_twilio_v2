"""Tests for conditions with several comparisons, > and <, and number ranges.

The compiler turns each into Studio splits nobody reads on the canvas, so the
splits are pinned here: which comparison each one makes, where a match and a
miss go, and what number Studio actually compares. A condition that compiles to
the wrong branch, or a range that compares "3,5" as text, fails in the data
weeks later and nowhere else.
"""

import pytest

from requests_to_twilio.compile import compile_spec, placeholder_sids
from requests_to_twilio.flows import check_flow, evaluate_condition
from requests_to_twilio.spec import (
    check_spec,
    parse_condition,
    parse_relevance,
    starter_spec,
)
from requests_to_twilio.spec_xlsx import read_xlsx, write_xlsx
from requests_to_twilio.widgets import placeholder_functions

#: Widgets every flow has, whatever its questions.
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


def next_of(state, event):
    return next(t["next"] for t in state["transitions"] if t["event"] == event)


def matches(state):
    """Return each (condition type, value, next) a split's match branches carry."""
    return [
        (c["type"], c["value"], t["next"])
        for t in state["transitions"]
        if t["event"] == "match"
        for c in t["conditions"]
    ]


def survey(**q2):
    spec = starter_spec("en")
    for key, value in q2.items():
        setattr(spec.row("Q2"), key, value)
    return spec


# -- the grammar ---------------------------------------------------------------


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("${a}='1'", ("", [("a", "=", "1")])),
            ("${a}='1' and ${b}!='2'", ("and", [("a", "=", "1"), ("b", "!=", "2")])),
            ("${a}>3 OR ${b}<'1,5'", ("or", [("a", ">", "3"), ("b", "<", "1,5")])),
            ("${days} > 0", ("", [("days", ">", "0")])),
        ],
    )
    def test_reads_what_the_builder_and_excel_write(self, text, expected):
        assert parse_relevance(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "${a}='1' and ${b}='2' or ${c}='3'",  # mixes and with or
            "selected(${a}, '1')",
            "${a}='1' and",
            "${a}>three",
        ],
    )
    def test_refuses_what_it_cannot_compile(self, text):
        assert parse_relevance(text) is None

    def test_a_closing_still_takes_only_one_equality(self):
        assert parse_condition("${outcome}='complete'") == ("outcome", "=", "complete")
        assert parse_condition("${outcome}>'1'") is None
        assert parse_condition("${outcome}='a' and ${x}='b'") is None


class TestTheSpecCheck:
    def test_more_than_needs_a_number_question(self):
        spec = survey(relevance="${Q1}>1")
        assert any("not a number question" in p for p in check_spec(spec))

    def test_more_than_needs_a_number(self):
        spec = survey()
        spec.row("Q3").relevance = "${Q2}>'many'"
        assert any("not a number" in p for p in check_spec(spec))

    def test_mixing_and_with_or_says_how_to_do_it(self):
        spec = survey()
        spec.row("Q3").relevance = "${Q2}>1 and ${Q1}='1' or ${Q1}='2'"
        assert any("mixes and with or" in p for p in check_spec(spec))

    def test_a_number_question_and_a_choice_combine(self):
        spec = survey()
        spec.row("Q3").relevance = "${Q2}>0 and ${Q1}='1'"
        assert check_spec(spec) == []


# -- conditions in the flow --------------------------------------------------------


class TestChainedConditions:
    def test_and_needs_every_comparison(self):
        spec = survey()
        spec.row("Q3").relevance = "${Q2}>0 and ${Q1}='1'"
        flow = states(build(spec))
        first, second = flow["if_Q3"], flow["if_Q3_2"]
        assert matches(first) == [("greater_than", "0", "if_Q3_2")]
        assert matches(second) == [("equal_to", "1", "Q3")]
        # Either failing skips the question, to the same place.
        assert next_of(first, "noMatch") == next_of(second, "noMatch") != "Q3"

    def test_or_needs_any_one(self):
        spec = survey()
        spec.row("Q3").relevance = "${Q1}='1' or ${Q1}='2'"
        flow = states(build(spec))
        assert matches(flow["if_Q3"]) == [("equal_to", "1", "Q3")]
        assert next_of(flow["if_Q3"], "noMatch") == "if_Q3_2"
        assert matches(flow["if_Q3_2"]) == [("equal_to", "2", "Q3")]
        assert next_of(flow["if_Q3_2"], "noMatch") != "Q3"

    def test_more_than_compares_a_number(self):
        """Studio compares numbers, not text: the value is converted first."""
        spec = survey()
        spec.row("Q3").relevance = "${Q2}>0"
        condition = states(build(spec))["if_Q3"]["transitions"][1]["conditions"][0]
        assert condition["arguments"] == [
            "{{flow.variables.Q2_value | strip | replace: ',', '.' | plus: 0}}"
        ]

    def test_the_widget_count_counts_each_comparison(self):
        spec = survey(minimum=0, maximum=7)
        spec.row("Q3").relevance = "${Q2}>0 and ${Q1}='1'"
        flow = build(spec)
        rows = sum(spec.widget_count(row) for row in spec.survey)
        assert rows + len(SPINE & set(states(flow))) == len(flow["states"])

    def test_the_flow_passes_its_checks(self):
        spec = survey(minimum=0, maximum=7)
        spec.row("Q3").relevance = "${Q2}>0 or ${Q1}='1'"
        assert check_flow(build(spec)) == []


# -- ranges ------------------------------------------------------------------------


class TestRanges:
    def test_a_number_outside_the_range_is_asked_again(self):
        flow = states(build(survey(minimum=0, maximum=7)))
        assert next_of(flow["split_Q2"], "match") == "range_Q2"
        assert matches(flow["range_Q2"]) == [
            ("less_than", "0", "retry_Q2"),
            ("greater_than", "7", "retry_Q2"),
        ]
        assert next_of(flow["range_Q2"], "noMatch") == "store_Q2"

    def test_the_limits_themselves_are_taken(self):
        """Studio's comparisons are strict, so refusing below and above keeps 0 and 7."""
        for reply, refused in (("-1", True), ("0", False), ("7", False), ("15", True)):
            below = evaluate_condition("less_than", "0", reply)
            above = evaluate_condition("greater_than", "7", reply)
            assert (below or above) is refused, reply

    def test_one_side_can_stay_open(self):
        flow = states(build(survey(maximum=7)))
        assert matches(flow["range_Q2"]) == [("greater_than", "7", "retry_Q2")]

    def test_the_reply_says_which_numbers_it_takes(self):
        flow = states(build(survey(minimum=0, maximum=7)))
        assert flow["error_Q2"]["properties"]["body"] == (
            "Please reply with a number from 0 to 7."
        )

    def test_a_decimal_is_stored_with_a_point(self):
        spec = survey(type="decimal")
        store = states(build(spec))["store_Q2"]
        value = next(
            v["value"]
            for v in store["properties"]["variables"]
            if v["key"] == "Q2_value"
        )
        assert value.endswith("| strip | replace: ',', '.'}}")

    @pytest.mark.parametrize(
        ("fields", "problem"),
        [
            ({"minimum": 8, "maximum": 7}, "above the maximum"),
            ({"minimum": -3}, "refuses a minus sign"),
            ({"minimum": 0.5}, "not a whole number"),
        ],
    )
    def test_the_check_refuses_a_range_that_cannot_work(self, fields, problem):
        assert any(problem in p for p in check_spec(survey(**fields)))

    def test_a_range_needs_its_reply(self):
        spec = survey(minimum=0, maximum=7)
        spec.messages = [m for m in spec.messages if m.key != "error_range"]
        assert any("error_range" in p for p in check_spec(spec))

    def test_only_a_number_question_takes_a_range(self):
        spec = starter_spec("en")
        spec.row("Q3").maximum = 7
        assert any("only to a number question" in p for p in check_spec(spec))

    def test_the_range_survives_the_workbook(self, tmp_path):
        spec = survey(type="decimal", minimum=0, maximum=7.5)
        path = tmp_path / "s.xlsx"
        write_xlsx(spec, path)
        q2 = read_xlsx(path).row("Q2")
        assert (q2.minimum, q2.maximum) == (0, 7.5)


@pytest.mark.parametrize(
    ("kind", "value", "reply", "expected"),
    [
        ("greater_than", "3", "4", True),
        ("greater_than", "3", "3", False),
        ("less_than", "3.5", "3,4", True),
        ("less_than", "3", "abc", False),
        ("greater_than", "3", "", False),
    ],
)
def test_the_evaluator_compares_numbers_like_studio(kind, value, reply, expected):
    assert evaluate_condition(kind, value, reply) is expected
