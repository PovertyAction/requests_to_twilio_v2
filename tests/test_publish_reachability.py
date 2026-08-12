"""Tests for break-off paths that never reach the publish widget.

Every launched respondent must end with exactly one published row carrying a
final status - complete, no_reply, fail, multierror, or consent declined. A path
that leaves the flow without passing through the publish widget produces no row
at all, so a break-off becomes indistinguishable from someone never contacted.

Nothing errors when that happens. These tests are how the defect gets caught.
"""

from requests_to_twilio.flows import unpublished_paths


def widget(name, kind="send-and-wait-for-reply", **transitions):
    """Build a widget whose transitions are given as event=destination."""
    return {
        "name": name,
        "type": kind,
        "properties": {},
        "transitions": [
            {"event": event, "next": destination}
            for event, destination in transitions.items()
        ],
    }


PUBLISH = {"name": "publish_gsheets", "type": "run-function", "transitions": []}


def test_no_publish_widget_reports_nothing():
    """A flow that never publishes is a different problem, not this one."""
    definition = {"states": [widget("q1", reply="q2", timeout="bye")]}
    assert unpublished_paths(definition) == []


def test_timeout_reaching_publish_is_fine():
    definition = {
        "states": [
            widget("q1", reply="publish_gsheets", timeout="mark_no_reply"),
            widget("mark_no_reply", kind="set-variables", next="publish_gsheets"),
            PUBLISH,
        ]
    }
    assert unpublished_paths(definition) == []


def test_timeout_that_never_publishes_is_reported():
    definition = {
        "states": [
            widget("q1", reply="publish_gsheets", timeout="dead_end"),
            widget("dead_end", kind="send-message"),
            PUBLISH,
        ]
    }
    assert unpublished_paths(definition) == [("q1", "timeout", "dead_end")]


def test_delivery_failure_is_checked_too():
    definition = {
        "states": [
            widget("q1", reply="publish_gsheets", deliveryFailure="gone"),
            widget("gone", kind="send-message"),
            PUBLISH,
        ]
    }
    assert unpublished_paths(definition) == [("q1", "deliveryFailure", "gone")]


def test_transition_with_no_destination_is_a_dead_end():
    definition = {
        "states": [
            {
                "name": "q1",
                "type": "send-and-wait-for-reply",
                "transitions": [
                    {"event": "reply", "next": "publish_gsheets"},
                    {"event": "timeout", "next": None},
                ],
            },
            PUBLISH,
        ]
    }
    assert unpublished_paths(definition) == [("q1", "timeout", "<dead end>")]


def test_indirect_route_through_several_widgets_counts():
    """Reachability is transitive - a long tail still publishes."""
    definition = {
        "states": [
            widget("q1", reply="publish_gsheets", timeout="a"),
            widget("a", kind="set-variables", next="b"),
            widget("b", kind="send-message", sent="c"),
            widget("c", kind="set-variables", next="publish_gsheets"),
            PUBLISH,
        ]
    }
    assert unpublished_paths(definition) == []


def test_loop_that_never_escapes_is_reported():
    """A reminder loop with no exit to publish strands everyone in it."""
    definition = {
        "states": [
            widget("q1", reply="publish_gsheets", timeout="rem1"),
            widget("rem1", timeout="rem2"),
            widget("rem2", timeout="rem1"),
            PUBLISH,
        ]
    }
    stranded = unpublished_paths(definition)
    assert ("q1", "timeout", "rem1") in stranded
    # The widgets inside the loop are stranded too.
    assert len(stranded) == 3


def test_publish_named_publish_is_recognised():
    """Both house names are used: publish_gsheets and plain publish."""
    definition = {
        "states": [
            widget("q1", reply="publish", timeout="publish"),
            {"name": "publish", "type": "run-function", "transitions": []},
        ]
    }
    assert unpublished_paths(definition) == []


def test_reply_transitions_are_not_checked():
    """Only break-off events matter; a reply path that skips publish is a
    different design question, not a silent data-loss bug.
    """
    definition = {
        "states": [
            widget("q1", reply="elsewhere", timeout="publish_gsheets"),
            widget("elsewhere", kind="send-message"),
            PUBLISH,
        ]
    }
    assert unpublished_paths(definition) == []
