"""Tests for evaluating Studio split conditions.

These pin down Studio's documented semantics so a flow's routing can be executed
in a test rather than read and hoped about. Reading a condition is how an
unreachable option survives review: it looks correct on the canvas, and the only
symptom is a column of noMatch after the round.
"""

from requests_to_twilio.flows import (
    check_flow,
    evaluate_condition,
    route_split,
    unmatchable_conditions,
)


def condition(kind, value, **extra):
    return {"friendly_name": extra.get("name", kind), "type": kind, "value": value}


def split(*branches, default="fallback"):
    """Build a split whose match transitions carry the given conditions."""
    transitions = [{"event": "noMatch", "next": default}]
    for destination, cond in branches:
        transitions.append(
            {"event": "match", "next": destination, "conditions": [cond]}
        )
    return {
        "name": "test_split",
        "type": "split-based-on",
        "properties": {"input": "{{widgets.q.inbound.Body}}"},
        "transitions": transitions,
    }


class TestStudioSemantics:
    """Studio trims whitespace and ignores case. Both are documented."""

    def test_matching_ignores_case(self):
        assert evaluate_condition("equal_to", "Yes", "yes")
        assert evaluate_condition("matches_any_of", "Yes,No", "YES")
        assert evaluate_condition("regex", "(?:yes)", "YES")

    def test_matching_trims_whitespace(self):
        assert evaluate_condition("equal_to", "yes", "  yes  ")
        assert evaluate_condition("matches_any_of", "yes,no", "\tyes\n")

    def test_regex_must_match_the_entire_string(self):
        assert evaluate_condition("regex", "(?:yes)", "yes")
        assert not evaluate_condition("regex", "(?:yes)", "yes please")
        assert not evaluate_condition("regex", "(?:yes)", "oh yes")

    def test_an_uncompilable_regex_matches_nothing(self):
        """Studio accepts it; it simply never fires."""
        assert not evaluate_condition("regex", "(unclosed", "anything")

    def test_unknown_condition_types_do_not_match(self):
        assert not evaluate_condition("is_a_tuesday", "x", "x")


class TestMatchesAnyOfIsCommaDelimited:
    """The trap that made this whole module worth writing."""

    def test_alternatives_are_split_on_commas(self):
        assert evaluate_condition("matches_any_of", "a,b,c", "b")

    def test_a_comma_inside_a_label_breaks_it_silently(self):
        """The label 'yes, often' becomes two alternatives, neither of them it."""
        value = "yes, often,no"
        assert not evaluate_condition("matches_any_of", value, "yes, often")
        assert evaluate_condition("matches_any_of", value, "yes")
        assert evaluate_condition("matches_any_of", value, "often")

    def test_regex_has_no_such_problem(self):
        assert evaluate_condition("regex", "(?:yes, often|no)", "yes, often")


class TestRouteSplit:
    def test_routes_to_the_first_matching_branch(self):
        state = split(
            ("a", condition("equal_to", "1")), ("b", condition("equal_to", "2"))
        )
        assert route_split(state, "1") == "a"
        assert route_split(state, "2") == "b"

    def test_falls_back_to_no_match(self):
        state = split(("a", condition("equal_to", "1")))
        assert route_split(state, "banana") == "fallback"

    def test_transition_order_decides_an_ambiguous_reply(self):
        """Which is exactly why consent must never have an ambiguous reply."""
        state = split(
            ("yes", condition("regex", "(?:s.*)")),
            ("no", condition("regex", "(?:si)")),
        )
        assert route_split(state, "si") == "yes"

    def test_a_split_with_no_no_match_dead_ends(self):
        state = {
            "name": "s",
            "type": "split-based-on",
            "properties": {},
            "transitions": [
                {
                    "event": "match",
                    "next": "a",
                    "conditions": [condition("equal_to", "1")],
                }
            ],
        }
        assert route_split(state, "9") is None


class TestUnmatchableConditions:
    def test_an_uncompilable_regex_is_reported(self):
        definition = {"states": [split(("a", condition("regex", "(unclosed")))]}
        assert unmatchable_conditions(definition)
        assert "unmatchable-condition" in {f.code for f in check_flow(definition)}

    def test_a_trailing_comma_is_reported(self):
        definition = {"states": [split(("a", condition("matches_any_of", "yes,no,")))]}
        problems = unmatchable_conditions(definition)
        assert problems and "empty alternative" in problems[0]

    def test_a_healthy_split_reports_nothing(self):
        definition = {
            "states": [
                split(
                    ("a", condition("matches_any_of", "yes,no")),
                    ("b", condition("regex", "(?:maybe)")),
                )
            ]
        }
        assert unmatchable_conditions(definition) == []
