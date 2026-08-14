"""Tests for the checks on the message that opens a conversation.

A business-initiated WhatsApp conversation can only be opened by an approved
template. Get this wrong and the round does not degrade - it fails at the very
first message, for every respondent at once, before any other widget runs. That
makes it the one defect worth catching structurally rather than from the error
logs afterwards.
"""

import json

import pytest

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


class TestTextLimits:
    """The character caps are short enough to shape question design.

    24 characters is shorter than most people's first draft of an answer option,
    and past the limit the create call fails with a generic error that does not
    say which string was too long.
    """

    def picker(self, **overrides):
        config = {
            "body": "How satisfied were you?",
            "button": "Rate 1 to 5",
            "items": [
                {"id": "s1", "item": "1 - Very bad", "description": "Not at all"},
                {"id": "s2", "item": "2 - Good", "description": "Mostly happy"},
            ],
        }
        config.update(overrides)
        return (
            {
                "states": [
                    trigger(incomingRequest="intro"),
                    template_question("intro", sid="HXopener", incomingMessage="q2"),
                    template_question("q2", sid="HXlist"),
                ]
            },
            {
                "HXopener": {"twilio/quick-reply": {}},
                "HXlist": {"twilio/list-picker": config},
            },
        )

    def test_a_short_scale_passes(self):
        definition, types = self.picker()
        assert "text-too-long" not in codes(definition, types)

    def test_an_item_over_24_chars_is_an_error(self):
        """The standard Likert midpoint is 26 characters, so this bites."""
        definition, types = self.picker(
            items=[
                {"id": "s3", "item": "Neither agree nor disagree", "description": "ok"}
            ]
        )
        assert "text-too-long" in codes(definition, types)

    def test_a_description_over_72_chars_is_an_error(self):
        definition, types = self.picker(
            items=[{"id": "s1", "item": "Fine", "description": "x" * 73}]
        )
        assert "text-too-long" in codes(definition, types)

    def test_a_body_over_1024_chars_is_an_error(self):
        definition, types = self.picker(body="x" * 1025)
        assert "text-too-long" in codes(definition, types)

    def test_a_long_button_is_only_a_warning(self):
        """Twilio documents no limit for it, so this is Meta's, not theirs."""
        definition, types = self.picker(button="Tap here to choose your answer")
        found = codes(definition, types)
        assert "text-may-truncate" in found
        assert "text-too-long" not in found

    def test_a_quick_reply_title_over_25_chars_is_an_error(self):
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro", sid="HXopener"),
            ]
        }
        types = {
            "HXopener": {
                "twilio/quick-reply": {"actions": [{"title": "y" * 26, "id": "yes"}]}
            }
        }
        assert "text-too-long" in codes(definition, types)

    def test_the_offending_string_is_named(self):
        """A generic failure is exactly what this check exists to replace."""
        definition, types = self.picker(
            items=[
                {"id": "s1", "item": "Neither agree nor disagree", "description": "ok"}
            ]
        )
        finding = next(
            f for f in check_flow(definition, types) if f.code == "text-too-long"
        )
        detail = " ".join(finding.detail)
        assert "Neither agree nor disagree" in detail
        assert "26" in detail


class TestInboundRouting:
    """Which flow owns a number's inbound webhook decides where replies land.

    Getting this wrong produces no error anywhere: messages send, respondents
    reply, a different flow answers them, and the tracker reports total success
    while the round collects nothing.
    """

    def client_with(self, phone, sms_url):
        from types import SimpleNamespace

        number = SimpleNamespace(phone_number=phone, sms_url=sms_url)
        return SimpleNamespace(
            incoming_phone_numbers=SimpleNamespace(list=lambda limit=200: [number])
        )

    def test_a_plain_number_reads_the_sms_webhook(self):
        """Only for SMS. A whatsapp: address goes to the sender instead."""
        from requests_to_twilio.flows import inbound_flow_sid

        sid = "FW" + "a" * 32
        client = self.client_with(
            "+15555550199", f"https://webhooks.twilio.com/v1/Accounts/ACx/Flows/{sid}"
        )
        assert inbound_flow_sid(client, "+15555550199") == sid

    def whatsapp_client(self, sender_id, callback_url):
        """Build a client whose senders endpoint returns one WhatsApp sender."""
        from types import SimpleNamespace

        payload = {
            "senders": [
                {"sender_id": sender_id, "webhook": {"callback_url": callback_url}}
            ]
        }
        return SimpleNamespace(
            request=lambda method, url, params=None: SimpleNamespace(
                status_code=200, text=json.dumps(payload)
            )
        )

    def test_a_whatsapp_address_reads_the_sender_not_the_number(self):
        """The number's sms_url governs SMS. Checking it for WhatsApp is a
        wrong answer dressed as a green light - the bug that let a broken
        round report "inbound routing OK".
        """
        from requests_to_twilio.flows import inbound_flow_sid

        sender_flow = "FW" + "b" * 32
        client = self.whatsapp_client("whatsapp:+15555550199", f"/Flows/{sender_flow}")
        assert inbound_flow_sid(client, "whatsapp:+15555550199") == sender_flow

    def test_an_unknown_whatsapp_sender_is_none(self):
        from requests_to_twilio.flows import inbound_flow_sid

        client = self.whatsapp_client("whatsapp:+19999999", "/Flows/FW" + "e" * 32)
        assert inbound_flow_sid(client, "whatsapp:+15555550199") is None

    def test_a_sender_with_no_studio_webhook_is_none(self):
        from requests_to_twilio.flows import inbound_flow_sid

        client = self.whatsapp_client("whatsapp:+15550100", "https://example.org/in")
        assert inbound_flow_sid(client, "whatsapp:+15550100") is None

    def test_a_senders_api_failure_is_a_flow_error(self):
        """Better to say the check could not run than to guess."""
        from types import SimpleNamespace

        from requests_to_twilio.flows import FlowError, inbound_flow_sid

        client = SimpleNamespace(
            request=lambda method, url, params=None: SimpleNamespace(
                status_code=500, text=""
            )
        )
        with pytest.raises(FlowError, match="Could not list WhatsApp senders"):
            inbound_flow_sid(client, "whatsapp:+15550100")

    def test_a_non_studio_webhook_is_unknown_not_wrong(self):
        """A Messaging Service or custom URL means we cannot tell."""
        from requests_to_twilio.flows import inbound_flow_sid

        client = self.client_with("+15550100", "https://example.org/inbound")
        assert inbound_flow_sid(client, "+15550100") is None

    def test_an_unknown_number_is_none(self):
        from requests_to_twilio.flows import inbound_flow_sid

        client = self.client_with("+15550100", "/Flows/FW" + "c" * 32)
        assert inbound_flow_sid(client, "+19999999") is None

    def test_a_blank_number_is_none(self):
        from requests_to_twilio.flows import inbound_flow_sid

        client = self.client_with("+15550100", "/Flows/FW" + "d" * 32)
        assert inbound_flow_sid(client, "") is None


class TestRespondentInitiatedStart:
    """IPA launches rounds; respondents do not start them.

    A flow that can be begun by writing to the number gets started by people
    being polite after they finish, and those executions carry no preloaded
    data - so they publish rows with no caseid to join back to the frame.
    """

    def test_inbound_reaching_a_question_is_flagged(self):
        definition = {
            "states": [
                trigger(incomingRequest="intro", incomingMessage="intro"),
                template_question("intro", incomingMessage="q2"),
                body_question("q2"),
            ]
        }
        assert "respondent-initiated-start" in codes(definition)

    def test_a_terminal_acknowledgement_is_not_flagged(self):
        """The house pattern: say one thing, end, do not start the survey."""
        definition = {
            "states": [
                trigger(incomingRequest="intro", incomingMessage="ack"),
                {
                    "name": "ack",
                    "type": "send-message",
                    "properties": {"body": "Thanks for your message."},
                    "transitions": [{"event": "sent"}, {"event": "failed"}],
                },
                template_question("intro"),
            ]
        }
        assert "respondent-initiated-start" not in codes(definition)

    def test_an_unrouted_inbound_is_not_flagged(self):
        definition = {
            "states": [
                trigger(incomingRequest="intro"),
                template_question("intro"),
            ]
        }
        assert "respondent-initiated-start" not in codes(definition)

    def test_it_walks_through_silent_widgets(self):
        definition = {
            "states": [
                trigger(incomingRequest="intro", incomingMessage="flag"),
                {
                    "name": "flag",
                    "type": "set-variables",
                    "properties": {},
                    "transitions": [{"event": "next", "next": "intro"}],
                },
                template_question("intro"),
            ]
        }
        assert "respondent-initiated-start" in codes(definition)
