"""Tests for break-off paths that never reach the publish widget.

Every launched respondent must end with exactly one published row carrying a
final status - complete, no_reply, fail, multierror, or consent declined. A path
that leaves the flow without passing through the publish widget produces no row
at all, so a break-off becomes indistinguishable from someone never contacted.

Nothing errors when that happens. These tests are how the defect gets caught.
"""

from requests_to_twilio.flows import (
    discarded_paths,
    publish_failure_reaches_message,
    unpublished_paths,
)


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


def test_break_off_check_stays_scoped_to_break_off_events():
    """`unpublished_paths` reports break-offs, and only break-offs.

    It is the error-level check, so it stays narrow: a missing timeout row is
    unambiguously data loss. Everything else is `discarded_paths`, below, at
    warning level.
    """
    definition = {
        "states": [
            widget("q1", reply="elsewhere", timeout="publish_gsheets"),
            widget("elsewhere", kind="send-message"),
            PUBLISH,
        ]
    }
    assert unpublished_paths(definition) == []


class TestDiscardedPaths:
    """The blind spot that let a publish failure check clean.

    This file used to assert that reply transitions were deliberately not
    checked. That assertion was why a flow could route `publish --fail-->
    closing_message` and report zero findings: the publish step reported its
    failure correctly, the flow discarded it, the respondent was thanked, and no
    row was written. The rule is now the inverse - any branch that gives up the
    chance to publish is reported.
    """

    def test_reply_path_that_skips_publish_is_reported(self):
        definition = {
            "states": [
                widget("q1", reply="elsewhere", timeout="publish_gsheets"),
                widget("elsewhere", kind="send-message"),
                PUBLISH,
            ]
        }
        assert ("q1", "reply", "elsewhere") in discarded_paths(definition)

    def test_the_publish_widgets_own_branches_are_left_alone(self):
        """Those belong to `publish_failure_reaches_message`.

        Reporting the failure branch here would be a tautology - if publishing
        is what failed, no onward path can publish either. The question worth
        asking is whether the respondent is told the survey landed.
        """
        definition = {
            "states": [
                widget("q1", reply="publish_gsheets", timeout="publish_gsheets"),
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "transitions": [
                        {"event": "success", "next": "closing"},
                        {"event": "fail", "next": "closing"},
                    ],
                },
                widget("closing", kind="send-message"),
            ]
        }
        assert [
            f for f in discarded_paths(definition) if f[0] == "publish_gsheets"
        ] == []

    def test_widgets_downstream_of_publish_owe_nothing(self):
        """Everything after the publish step ends without publishing again."""
        definition = {
            "states": [
                widget("q1", reply="publish_gsheets", timeout="publish_gsheets"),
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "transitions": [{"event": "success", "next": "closing"}],
                },
                widget("closing", kind="send-message"),
            ]
        }
        assert discarded_paths(definition) == []

    def test_dead_end_transition_is_reported(self):
        definition = {
            "states": [
                {
                    "name": "q1",
                    "type": "split-based-on",
                    "transitions": [
                        {"event": "match", "next": "publish_gsheets"},
                        {"event": "noMatch", "next": None},
                    ],
                },
                PUBLISH,
            ]
        }
        assert ("q1", "noMatch", "<dead end>") in discarded_paths(definition)

    def test_trigger_routing_is_left_to_its_own_check(self):
        """A cold inbound ending without a row is `respondent-initiated-start`."""
        definition = {
            "states": [
                {
                    "name": "Trigger",
                    "type": "trigger",
                    "transitions": [
                        {"event": "incomingRequest", "next": "q1"},
                        {"event": "incomingMessage", "next": "brush_off"},
                    ],
                },
                widget("q1", reply="publish_gsheets", timeout="publish_gsheets"),
                widget("brush_off", kind="send-message"),
                PUBLISH,
            ]
        }
        assert [f for f in discarded_paths(definition) if f[0] == "Trigger"] == []

    def test_flow_without_a_publish_widget_reports_nothing(self):
        """A flow with no publish step is a different finding entirely."""
        definition = {"states": [widget("q1", reply=None)]}
        assert discarded_paths(definition) == []


class TestMessyGraphs:
    """A Studio canvas accumulates debris, and checks have to survive it.

    Widgets get duplicated while editing, branches get rewired and their old
    destination left behind, and a rename can leave a transition pointing at a
    name that no longer exists. None of that is visible to a respondent, so
    none of it should produce a finding - a check that fires on dead canvas
    furniture is a check people learn to ignore.
    """

    def _flow(self, states, initial="q1"):
        return {"states": states, "initial_state": initial}

    def test_an_orphaned_publish_copy_is_ignored(self):
        """Studio's duplicate is named `..._copy` and still matches by name.

        Left in `publishers` it would drag everything downstream of the real
        publish step into `reaching`, turning correct terminal paths into
        findings - and would itself be reported for the failure branch it
        inherited, on a widget no execution can enter.
        """
        definition = self._flow(
            [
                widget("q1", reply="publish_gsheets", timeout="publish_gsheets"),
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "transitions": [{"event": "success", "next": "closing"}],
                },
                widget("closing", kind="send-message"),
                # Unreachable: nothing transitions to it.
                {
                    "name": "publish_gsheets_copy",
                    "type": "run-function",
                    "transitions": [
                        {"event": "success", "next": "closing"},
                        {"event": "fail", "next": "closing"},
                    ],
                },
            ]
        )
        assert publish_failure_reaches_message(definition) == []
        assert discarded_paths(definition) == []
        assert unpublished_paths(definition) == []

    def test_a_second_live_publisher_is_honoured(self):
        """Two real publish widgets on different branches is legitimate."""
        definition = self._flow(
            [
                {
                    "name": "q1",
                    "type": "split-based-on",
                    "transitions": [
                        {"event": "match", "next": "publish_a"},
                        {"event": "noMatch", "next": "publish_b"},
                    ],
                },
                {"name": "publish_a", "type": "run-function", "transitions": []},
                {"name": "publish_b", "type": "run-function", "transitions": []},
            ]
        )
        assert discarded_paths(definition) == []

    def test_a_self_loop_does_not_hang_or_report(self):
        definition = self._flow(
            [
                widget("q1", reply="publish_gsheets", timeout="q1"),
                PUBLISH,
            ]
        )
        assert discarded_paths(definition) == []
        assert unpublished_paths(definition) == []

    def test_a_transition_to_a_nonexistent_widget_is_reported(self):
        """Usually a rename that missed one edge. It is still a lost row."""
        definition = self._flow(
            [
                {
                    "name": "q1",
                    "type": "split-based-on",
                    "transitions": [
                        {"event": "match", "next": "publish_gsheets"},
                        {"event": "noMatch", "next": "store_q1_typo"},
                    ],
                },
                PUBLISH,
            ]
        )
        assert ("q1", "noMatch", "store_q1_typo") in discarded_paths(definition)

    def test_a_widget_after_the_publish_step_owes_nothing(self):
        """Its row was written before it ran, so its dead ends are not losses."""
        definition = self._flow(
            [
                widget("q1", reply="publish_gsheets", timeout="publish_gsheets"),
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "transitions": [{"event": "success", "next": "followup"}],
                },
                widget("followup", timeout=None),
            ]
        )
        assert unpublished_paths(definition) == []

    def test_a_state_without_a_name_does_not_crash(self):
        definition = self._flow(
            [
                widget("q1", reply="publish_gsheets", timeout="publish_gsheets"),
                PUBLISH,
                {"type": "set-variables", "transitions": []},
            ]
        )
        assert discarded_paths(definition) == []


class TestPublishFailureReachesMessage:
    def test_failure_that_sends_a_message_is_caught(self):
        definition = {
            "states": [
                widget("q1", reply="publish_gsheets", timeout="publish_gsheets"),
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "transitions": [
                        {"event": "success", "next": "closing"},
                        {"event": "fail", "next": "closing"},
                    ],
                },
                widget("closing", kind="send-message"),
            ]
        }
        assert publish_failure_reaches_message(definition) == [
            ("publish_gsheets", "closing")
        ]

    def test_failure_routed_to_a_silent_end_is_fine(self):
        definition = {
            "states": [
                widget("q1", reply="publish_gsheets", timeout="publish_gsheets"),
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "transitions": [
                        {"event": "success", "next": "closing"},
                        {"event": "fail", "next": "record_failure"},
                    ],
                },
                widget("closing", kind="send-message"),
                {
                    "name": "record_failure",
                    "type": "set-variables",
                    "transitions": [{"event": "next", "next": None}],
                },
            ]
        }
        assert publish_failure_reaches_message(definition) == []
