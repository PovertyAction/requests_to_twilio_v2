"""Tests for the checks on the message that opens a conversation.

A business-initiated WhatsApp conversation can only be opened by an approved
template. Get this wrong and the round does not degrade - it fails at the very
first message, for every respondent at once, before any other widget runs. That
makes it the one defect worth catching structurally rather than from the error
logs afterwards.
"""

from requests_to_twilio.flows import check_flow, opening_sends


def trigger(**events):
    return {
        "name": "Trigger",
        "type": "trigger",
        "properties": {},
        "transitions": [{"event": e, "next": d} for e, d in events.items()],
    }


def template_question(name, sid="HXaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", **transitions):
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": {"message_type": "content_template", "content_sid": sid},
        "transitions": [{"event": e, "next": d} for e, d in transitions.items()],
    }


def body_question(name, **transitions):
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": {"body": "Hello there"},
        "transitions": [{"event": e, "next": d} for e, d in transitions.items()],
    }


def codes(definition, content_types=None):
    return {f.code for f in check_flow(definition, content_types)}


class TestOpeningSends:
    def test_finds_the_first_sending_widget(self):
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro", incomingMessage="q2"),
                body_question("q2"),
            ]
        }
        assert opening_sends(definition) == ["intro"]

    def test_walks_through_silent_widgets(self):
        """Variables and functions send nothing, so the opener is past them."""
        definition = {
            "states": [
                trigger(incomingRequest="setup"),
                {
                    "name": "setup",
                    "type": "set-variables",
                    "properties": {},
                    "transitions": [{"event": "next", "next": "intro"}],
                },
                template_question("intro"),
            ]
        }
        assert opening_sends(definition) == ["intro"]

    def test_follows_every_branch_of_a_split(self):
        definition = {
            "states": [
                trigger(incomingRequest="pick"),
                {
                    "name": "pick",
                    "type": "split-based-on",
                    "properties": {},
                    "transitions": [
                        {"event": "noMatch", "next": "intro_a"},
                        {"event": "match", "next": "intro_b"},
                    ],
                },
                template_question("intro_a"),
                body_question("intro_b"),
            ]
        }
        assert sorted(opening_sends(definition)) == ["intro_a", "intro_b"]

    def test_ignores_the_inbound_path(self):
        """A respondent who writes first has already opened the window."""
        definition = {
            "states": [
                trigger(incomingMessage="greet"),
                body_question("greet"),
            ]
        }
        assert opening_sends(definition) == []

    def test_stops_at_the_first_send(self):
        """Later messages are inside the window this one opens."""
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro", incomingMessage="q2"),
                body_question("q2", incomingMessage="q3"),
                body_question("q3"),
            ]
        }
        assert opening_sends(definition) == ["intro"]

    def test_survives_a_transition_to_a_missing_widget(self):
        definition = {
            "states": [
                trigger(incomingRequest="nowhere"),
                template_question("intro"),
            ]
        }
        assert opening_sends(definition) == []

    def test_survives_a_loop(self):
        definition = {
            "states": [
                trigger(incomingRequest="a"),
                {
                    "name": "a",
                    "type": "set-variables",
                    "properties": {},
                    "transitions": [{"event": "next", "next": "b"}],
                },
                {
                    "name": "b",
                    "type": "set-variables",
                    "properties": {},
                    "transitions": [{"event": "next", "next": "a"}],
                },
            ]
        }
        assert opening_sends(definition) == []


class TestOpeningChecks:
    def test_free_form_opening_is_an_error(self):
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                body_question("intro"),
            ]
        }
        assert "opening-not-a-template" in codes(definition)

    def test_template_opening_passes(self):
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro"),
            ]
        }
        assert "opening-not-a-template" not in codes(definition)

    def test_list_picker_cannot_open_a_session(self):
        """It is a fine reply and an impossible opener."""
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro", sid="HXlist"),
            ]
        }
        types = {"HXlist": {"twilio/text": {}, "twilio/list-picker": {}}}
        assert "opening-cannot-open-session" in codes(definition, types)

    def test_quick_reply_can_open_a_session(self):
        """Buttons only need approval, which is a different question."""
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro", sid="HXqr"),
            ]
        }
        types = {"HXqr": {"twilio/text": {}, "twilio/quick-reply": {}}}
        assert "opening-cannot-open-session" not in codes(definition, types)

    def test_unknown_content_types_are_skipped_not_guessed(self):
        """Without the Content API the check has nothing to say, and says it."""
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro", sid="HXunknown"),
            ]
        }
        assert "opening-cannot-open-session" not in codes(definition, {})

    def test_a_list_picker_later_in_the_flow_is_fine(self):
        """In session, list pickers are free - which is the whole point."""
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro", sid="HXqr", incomingMessage="q2"),
                template_question("q2", sid="HXlist"),
            ]
        }
        types = {
            "HXqr": {"twilio/quick-reply": {}},
            "HXlist": {"twilio/list-picker": {}},
        }
        assert "opening-cannot-open-session" not in codes(definition, types)

    def test_a_flow_with_no_trigger_reports_nothing(self):
        assert opening_sends({"states": []}) == []


class TestOptionLimits:
    """WhatsApp renders at most 10 list rows, and 3 buttons in session.

    Both are house rules as much as API limits. A question needing more than ten
    answers needs splitting, and options past the first few are options nobody
    scrolls to.
    """

    def flow_with(self, sid, **types):
        return (
            {
                "states": [
                    trigger(incomingRequest="intro"),
                    template_question("intro", sid="HXopener", incomingMessage="q2"),
                    template_question("q2", sid=sid),
                ]
            },
            {"HXopener": {"twilio/quick-reply": {}}, sid: types},
        )

    def test_eleven_list_rows_is_an_error(self):
        items = [{"id": str(i), "item": str(i), "description": "d"} for i in range(11)]
        definition, types = self.flow_with(
            "HXlist", **{"twilio/list-picker": {"items": items}}
        )
        assert "too-many-options" in codes(definition, types)

    def test_ten_list_rows_is_fine(self):
        items = [{"id": str(i), "item": str(i), "description": "d"} for i in range(10)]
        definition, types = self.flow_with(
            "HXlist", **{"twilio/list-picker": {"items": items}}
        )
        assert "too-many-options" not in codes(definition, types)

    def test_four_buttons_in_session_is_an_error(self):
        actions = [{"title": str(i), "id": str(i)} for i in range(4)]
        definition, types = self.flow_with(
            "HXqr", **{"twilio/quick-reply": {"actions": actions}}
        )
        assert "too-many-options" in codes(definition, types)

    def test_three_buttons_in_session_is_fine(self):
        actions = [{"title": str(i), "id": str(i)} for i in range(3)]
        definition, types = self.flow_with(
            "HXqr", **{"twilio/quick-reply": {"actions": actions}}
        )
        assert "too-many-options" not in codes(definition, types)

    def test_the_opener_may_carry_more_buttons(self):
        """It is approved, so the in-session ceiling does not apply to it."""
        actions = [{"title": str(i), "id": str(i)} for i in range(6)]
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro", sid="HXopener"),
            ]
        }
        types = {"HXopener": {"twilio/quick-reply": {"actions": actions}}}
        assert "too-many-options" not in codes(definition, types)

    def test_a_malformed_content_type_does_not_crash_the_check(self):
        definition, types = self.flow_with(
            "HXodd", **{"twilio/list-picker": "nonsense"}
        )
        assert "too-many-options" not in codes(definition, types)
