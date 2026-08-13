"""Build the RST Jaipur 2026 flow from the Foro Nacional de Datos flow.

The Foro flow is a randomised methods demonstration: participants are split into
two arms that receive the *same four questions* in different formats - ARM 1
open and numeric, ARM 2 numbered multiple choice - and the close reveals the
experiment. That design is the pedagogical payload and is preserved exactly;
only the language, the event framing and the plumbing change.

Three things force a rebuild rather than a text edit:

* The source flow comes from a different Twilio account. Its three content
  templates and both Functions services return 404 on IPA_Console_3, so nothing
  it references resolves here.
* Those templates carry the opening and both consent messages. Recreating them
  in English needs fresh Meta approval, which is the long pole. This build emits
  them as inline bodies, which work inside the 24-hour customer service window
  and are therefore testable today. Swap in approved templates for a real
  business-initiated round.
* The 17 `short_w_*` widgets are typing-pause helpers on a Functions service
  that does not exist here. They are dropped: 17 external dependencies that
  contribute nothing to the data.

Run with `just build-rst-flow`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from requests_to_twilio.flows import check_flow  # noqa: E402

OUTPUT = REPO_ROOT / "flows" / "RST2026_jaipur_data_use.json"

FROM = "{{flow.channel.address}}"
TIMEOUT = "3600"  # 1 hour, as in the source flow

#: rst2023_wa_session_intro - already approved on this account, en_US, carries a
#: {{1}} name variable and a Start button. Reused so the flow can be launched
#: business-initiated today rather than waiting on Meta to approve a new
#: template. Its text still says 2023, which is fine for a test and must be
#: replaced with an approved rst2026 template before a real round.
OPENING_TEMPLATE_SID = "HX10a14cb24093a9b56de154385a545640"

#: Filled in once the Functions are deployed on this account. Left as None so a
#: half-configured flow cannot be deployed by accident.
ENCRYPT_SERVICE_SID = "ZS04f75bf125e71003387d709e77f1f6ad"
ENCRYPT_ENVIRONMENT_SID = "ZE73bad56bc5cba5c3c4b5fe6bcba2dc92"
ENCRYPT_FUNCTION_SID = "ZH477834617e55e948fc9149388bf1ef63"
PUBLISH_SERVICE_SID = "ZS04f75bf125e71003387d709e77f1f6ad"
PUBLISH_ENVIRONMENT_SID = "ZE73bad56bc5cba5c3c4b5fe6bcba2dc92"
PUBLISH_FUNCTION_SID = "ZH08a4b1579e90c2e3a6ea7829a153e842"

#: Studio requires the deployed URL on a run-function widget, not just the
#: SIDs; validation rejects the flow without it.
FUNCTIONS_DOMAIN = "rtt-survey-2647-prod.twil.io"
ENCRYPT_URL = f"https://{FUNCTIONS_DOMAIN}/encrypt-fields"
PUBLISH_URL = f"https://{FUNCTIONS_DOMAIN}/publish-motherduck"

# --------------------------------------------------------------------------
# Message text. Translated from the Spanish original; the event framing is
# replaced with an RST debrief, since participants are already in the room and
# registration is handled elsewhere.
# --------------------------------------------------------------------------

INTRO = (
    "Hi {{flow.data.name}}, welcome to the Research Staff Training 2026 in Jaipur.\n\n"
    "You are about to take part in a real WhatsApp survey - and you are also "
    "part of the example.\n\n"
    "It takes about 3 minutes. Your answers are confidential and will only be "
    "used to illustrate WhatsApp data collection during the session."
)

CONSENT = (
    "Would you like to take part?\n\n"
    "Reply *1* for Yes, or *2* for No.\n\n"
    "Taking part is voluntary and you can stop at any time by not replying."
)

# ARM 1 - open and numeric. Deliberately harder to answer.
ARM1 = {
    "P1": (
        "In the last four (4) weeks, on how many occasions have you drawn on "
        "quantitative or qualitative data (for example databases, administrative "
        "records, statistical reports, surveys, dashboards) as an input to "
        "decisions related to your work?\n\n"
        "_Reply with an exact number (e.g. 0, 3, 12)._"
    ),
    "P2": (
        "In the last four (4) weeks, in how many projects, tasks or formal "
        "processes you took part in was data or empirical evidence used as "
        "explicit support for decision making (for example adjusting strategy, "
        "allocating resources, redesigning a process)?\n\n"
        "_Reply with an exact number (e.g. 0, 3, 12)._"
    ),
    "P3": (
        "Over the last year, how often did you, or the organisation you work "
        "for, carry out systematic data collection (for example running surveys, "
        "filling in forms, extracting information from internal systems, "
        "updating databases) that was then used as an input for decisions in "
        "your work?\n\n"
        '_Reply with a short phrase (e.g. "never", "sometimes", "almost '
        'always", or an approximate percentage)._'
    ),
    "P4": (
        "Over the last year, when you needed to collect information or data to "
        "support decisions in your work, what was the main mode of data "
        "collection you used?\n\n"
        "_For example: online surveys, paper forms, phone interviews, internal "
        "administrative records, analysis of existing databases._"
    ),
}

# ARM 2 - the same four constructs as numbered options. Deliberately easier.
ARM2 = {
    "P1": (
        "Q1. In the last 4 weeks, how many times have you used data to make "
        "decisions in your work?\n\n"
        "Please reply with ONLY the number of the option that best describes you:\n\n"
        "1  0 times\n2  1-2 times\n3  3-5 times\n4  6-10 times\n5  More than 10 times\n\n"
        "_Valid replies: 1, 2, 3, 4 or 5._"
    ),
    "P2": (
        "Q2. In the last 4 weeks, in how many projects was data used to make "
        "decisions?\n\n"
        "Please reply with ONLY the number of the option:\n\n"
        "1  0 projects\n2  1 project\n3  2-3 projects\n4  4-5 projects\n"
        "5  More than 5 projects\n\n"
        "_Valid replies: 1, 2, 3, 4 or 5._"
    ),
    "P3": (
        "Q3. Over the last year, how often did you or your organisation collect "
        "the data used to make decisions?\n\n"
        "Please reply with ONLY the number of the option:\n\n"
        "1  We never collect data\n2  In a few projects (less than 25%)\n"
        "3  In about half of projects (25%-50%)\n"
        "4  In most projects (51%-75%)\n5  In almost all projects (more than 75%)\n\n"
        "_Valid replies: 1, 2, 3, 4 or 5._"
    ),
    "P4": (
        "Q4. Over the last year, when you needed to collect information, which "
        "mode did you mainly use?\n\n"
        "Please reply with ONLY the number of the option:\n\n"
        "1  Mainly in-person surveys (CAPI)\n2  Mainly phone surveys (CATI)\n"
        "3  Mainly web/online surveys\n4  Mainly WhatsApp surveys\n5  Another mode\n\n"
        "_Valid replies: 1, 2, 3, 4 or 5._"
    ),
}

ERROR_NUMERIC = (
    "Please reply with a number only.\n\n"
    "_Reply with an exact number (e.g. 0, 3, 12)._\n\n"
    "I am a bot and cannot understand everything that is written to me."
)

ERROR_OPTION = (
    "Please reply with the number of your chosen option only.\n\n"
    "_Reply with one of 1, 2, 3, 4 or 5._\n\n"
    "I am a bot and cannot understand everything that is written to me."
)

# The close reveals the experiment, then points at the session. Registration is
# handled elsewhere, so this teases the results rather than recruiting.
CLOSE_COMPLETE = (
    "Thank you for completing the survey.\n\n"
    "You took part in an experiment with two different versions of the same "
    "survey: one using plain language with clear answer options, and one more "
    "open and complex.\n\n"
    "We want to show how the way a question is asked can change the answers - "
    "and the results - in WhatsApp surveys.\n\n"
    "We will show what your group's answers looked like, live in the session. "
    "See you there."
)

CLOSE_DECLINED = (
    "Thank you for your reply. We understand and respect your decision not to "
    "take part in the exercise.\n\n"
    "You are still very welcome in the session, where we will look at how "
    "WhatsApp data collection works and where it goes wrong."
)

CLOSE_INCOMPLETE = (
    "Thank you for the answers you gave. We have recorded them.\n\n"
    "We will look at how question wording changes survey answers, live in the "
    "session. See you there."
)


def send(name, body, next_state, *, x=0, y=0):
    """Build a one-way message widget."""
    return {
        "name": name,
        "type": "send-message",
        "properties": {"offset": {"x": x, "y": y}, "from": FROM, "body": body},
        "transitions": [
            {"event": "sent", "next": next_state},
            {"event": "failed", "next": next_state},
        ],
    }


def ask(name, body, on_reply, *, x=0, y=0, on_timeout="mark_no_reply"):
    """Build a question. Wires reply, timeout and deliveryFailure, as checks require."""
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": {
            "offset": {"x": x, "y": y},
            "from": FROM,
            "body": body,
            "timeout": TIMEOUT,
        },
        "transitions": [
            {"event": "incomingMessage", "next": on_reply},
            {"event": "timeout", "next": on_timeout},
            {"event": "deliveryFailure", "next": "mark_delivery_failed"},
        ],
    }


def set_vars(name, pairs, next_state, *, x=0, y=0):
    """Set literal flow variables."""
    return {
        "name": name,
        "type": "set-variables",
        "properties": {
            "offset": {"x": x, "y": y},
            "variables": [{"key": k, "value": v} for k, v in pairs],
        },
        "transitions": [{"event": "next", "next": next_state}],
    }


def split(name, tested, branches, default, *, x=0, y=0):
    """Branch on a value. `default` is the mandatory noMatch destination."""
    transitions = [{"event": "noMatch", "next": default}]
    for value, destination in branches:
        transitions.append(
            {
                "event": "match",
                "next": destination,
                "conditions": [
                    {
                        "friendly_name": f"is {value}",
                        "arguments": [tested],
                        "type": "equal_to",
                        "value": value,
                    }
                ],
            }
        )
    return {
        "name": name,
        "type": "split-based-on",
        "properties": {"offset": {"x": x, "y": y}, "input": tested},
        "transitions": transitions,
    }


def question_block(arm, key, body, *, numeric, y, next_state):
    """Build a question with validation, a bounded retry, and a give-up path.

    Three widgets per question, mirroring the account's house pattern: ask,
    validate, and a counter that decides between re-asking and moving on. The
    limit matters ethically as well as technically - re-asking someone who
    cannot answer is badgering a volunteer.
    """
    name = f"{arm}_{key}"
    validate = f"split_{name}"
    retry = f"retry_{name}"
    give_up = f"giveup_{name}"
    error_widget = f"error_{name}"

    options = ["1", "2", "3", "4", "5"]
    branches = [] if numeric else [(value, f"store_{name}") for value in options]

    states = [
        ask(name, body, validate, x=arm_x(arm), y=y),
    ]

    if numeric:
        # Free numeric or free text: accept whatever arrives. The source flow
        # did the same - validating open answers defeats the point of ARM 1.
        states.append(
            set_vars(
                f"store_{name}",
                [(f"{name}_status", "answered")],
                next_state,
                x=arm_x(arm),
                y=y + 80,
            )
        )
        states[0]["transitions"][0]["next"] = f"store_{name}"
    else:
        states.append(
            split(
                validate,
                f"{{{{widgets.{name}.inbound.Body}}}}",
                branches,
                retry,
                x=arm_x(arm),
                y=y + 80,
            )
        )
        states.append(
            set_vars(
                f"store_{name}",
                [(f"{name}_status", "answered")],
                next_state,
                x=arm_x(arm),
                y=y + 160,
            )
        )
        states.append(
            set_vars(
                retry,
                [
                    (
                        f"tries_{name}",
                        "{% assign current = flow.variables.tries_"
                        f"{name} | default: 0 %}}"
                        "{{ current | plus: 1 }}",
                    )
                ],
                f"check_{name}",
                x=arm_x(arm) + 260,
                y=y + 80,
            )
        )
        states.append(
            split(
                f"check_{name}",
                f"{{{{flow.variables.tries_{name}}}}}",
                [("1", error_widget), ("2", error_widget)],
                give_up,
                x=arm_x(arm) + 260,
                y=y + 160,
            )
        )
        states.append(
            send(error_widget, ERROR_OPTION, name, x=arm_x(arm) + 520, y=y + 160)
        )
        states.append(
            set_vars(
                give_up,
                [(f"{name}_status", "multierror")],
                next_state,
                x=arm_x(arm) + 260,
                y=y + 240,
            )
        )
    return states


def arm_x(arm):
    """Lay ARM 1 and ARM 2 out side by side in the Studio canvas."""
    return -700 if arm == "ARM1" else 500


def build():
    """Assemble the full flow definition."""
    states = [
        {
            "name": "Trigger",
            "type": "trigger",
            "properties": {"offset": {"x": 0, "y": -1100}},
            "transitions": [
                # `rtt launch` creates executions over the REST API, which fires
                # incomingRequest - NOT incomingMessage. Leaving it unrouted
                # ends the execution at the trigger without sending anything,
                # and the launcher still reports it as "active".
                {"event": "incomingRequest", "next": "intro"},
                # Someone messaging the number cold gets the same opening.
                {"event": "incomingMessage", "next": "intro"},
                {"event": "incomingCall"},
                {"event": "incomingConversationMessage"},
                {"event": "incomingParent"},
            ],
        },
        # The opening must be an approved template and must WAIT for a reply.
        # Anything sent before the respondent answers is business-initiated, so
        # a plain send here would let the next message go out while the session
        # is still closed and fail with 63016. Waiting means their reply opens
        # the 24-hour window and everything downstream can be free-form.
        {
            "name": "intro",
            "type": "send-and-wait-for-reply",
            "properties": {
                "offset": {"x": 0, "y": -950},
                "from": FROM,
                "message_type": "content_template",
                "content_sid": OPENING_TEMPLATE_SID,
                "content_variables": [{"key": "1", "value": "{{flow.data.name}}"}],
                "timeout": TIMEOUT,
            },
            "transitions": [
                {"event": "incomingMessage", "next": "consent"},
                {"event": "timeout", "next": "mark_no_reply"},
                {"event": "deliveryFailure", "next": "mark_delivery_failed"},
            ],
        },
        ask("consent", CONSENT, "split_consent", x=0, y=-820),
        split(
            "split_consent",
            "{{widgets.consent.inbound.Body}}",
            [("1", "record_consent"), ("2", "record_declined")],
            "record_declined",
            x=0,
            y=-700,
        ),
        set_vars(
            "record_consent",
            [("set_consent", "yes")],
            "split_arm",
            x=0,
            y=-580,
        ),
        set_vars(
            "record_declined",
            [("set_consent", "no"), ("outcome", "declined")],
            "finish",
            x=400,
            y=-580,
        ),
        # The arm is preloaded, not drawn here: randomisation happens offline in
        # the sample file, so it is reproducible and balance can be checked
        # before anyone is contacted.
        split(
            "split_arm",
            "{{flow.data.arm}}",
            [("1", "ARM1_P1"), ("2", "ARM2_P1")],
            "ARM1_P1",
            x=0,
            y=-460,
        ),
    ]

    for arm, texts, numeric in (("ARM1", ARM1, True), ("ARM2", ARM2, False)):
        keys = ["P1", "P2", "P3", "P4"]
        for index, key in enumerate(keys):
            following = (
                f"{arm}_{keys[index + 1]}" if index + 1 < len(keys) else "mark_complete"
            )
            states.extend(
                question_block(
                    arm,
                    key,
                    texts[key],
                    numeric=numeric,
                    y=-300 + index * 340,
                    next_state=following,
                )
            )

    states.extend(
        [
            set_vars(
                "mark_complete",
                [("set_complete", "1"), ("outcome", "complete")],
                "finish",
                x=0,
                y=1200,
            ),
            set_vars(
                "mark_no_reply",
                [("set_no_reply", "1"), ("outcome", "incomplete")],
                "finish",
                x=900,
                y=1200,
            ),
            set_vars(
                "mark_delivery_failed",
                [("set_fail", "1"), ("outcome", "incomplete")],
                "finish",
                x=1300,
                y=1200,
            ),
            # Single convergence point. Every terminal path arrives here, so a
            # row is published whatever happened - complete, declined, timed
            # out or undeliverable.
            set_vars(
                "finish",
                [("set_time_fin", "{{flow.variables.now}}")],
                "function_encrypt",
                x=0,
                y=1320,
            ),
        ]
    )

    states.append(
        {
            "name": "function_encrypt",
            "type": "run-function",
            "properties": {
                "offset": {"x": 0, "y": 1440},
                "service_sid": ENCRYPT_SERVICE_SID,
                "environment_sid": ENCRYPT_ENVIRONMENT_SID,
                "function_sid": ENCRYPT_FUNCTION_SID,
                "url": ENCRYPT_URL,
                "parameters": [
                    {"key": "enc_name", "value": "{{flow.data.name}}"},
                    {
                        "key": "enc_p_number_original",
                        "value": "{{contact.channel.address}}",
                    },
                ],
            },
            "transitions": [
                {"event": "success", "next": "publish_motherduck"},
                {"event": "fail", "next": "publish_motherduck"},
            ],
        }
    )

    published = [
        ("caseid", "{{flow.data.caseid}}"),
        ("arm", "{{flow.data.arm}}"),
        ("enc_name", "{{widgets.function_encrypt.parsed.enc_name}}"),
        (
            "enc_p_number_original",
            "{{widgets.function_encrypt.parsed.enc_p_number_original}}",
        ),
        ("set_consent", "{{flow.variables.set_consent}}"),
        ("set_complete", "{{flow.variables.set_complete}}"),
        ("set_no_reply", "{{flow.variables.set_no_reply}}"),
        ("set_fail", "{{flow.variables.set_fail}}"),
        ("outcome", "{{flow.variables.outcome}}"),
    ]
    for arm in ("ARM1", "ARM2"):
        for key in ("P1", "P2", "P3", "P4"):
            name = f"{arm}_{key}"
            published.append((name, f"{{{{widgets.{name}.inbound.Body}}}}"))
            published.append(
                (f"{name}_status", f"{{{{flow.variables.{name}_status}}}}")
            )

    states.append(
        {
            "name": "publish_motherduck",
            "type": "run-function",
            "properties": {
                "offset": {"x": 0, "y": 1560},
                "service_sid": PUBLISH_SERVICE_SID,
                "environment_sid": PUBLISH_ENVIRONMENT_SID,
                "function_sid": PUBLISH_FUNCTION_SID,
                "url": PUBLISH_URL,
                "parameters": [{"key": k, "value": v} for k, v in published],
            },
            "transitions": [
                {"event": "success", "next": "split_closing"},
                {"event": "fail", "next": "split_closing"},
            ],
        }
    )

    states.extend(
        [
            split(
                "split_closing",
                "{{flow.variables.outcome}}",
                [
                    ("complete", "close_complete"),
                    ("declined", "close_declined"),
                    ("incomplete", "close_incomplete"),
                ],
                "close_incomplete",
                x=0,
                y=1680,
            ),
            send("close_complete", CLOSE_COMPLETE, "close_complete", x=-400, y=1800),
            send("close_declined", CLOSE_DECLINED, "close_declined", x=0, y=1800),
            send(
                "close_incomplete", CLOSE_INCOMPLETE, "close_incomplete", x=400, y=1800
            ),
        ]
    )

    # The closing messages are terminal: drop their outgoing transitions so the
    # flow ends rather than looping back into itself.
    for state in states:
        if state["name"].startswith("close_"):
            state["transitions"] = [{"event": "sent"}, {"event": "failed"}]

    return {
        "description": "RST Jaipur 2026 - data use survey (ARM1/ARM2 experiment)",
        "states": states,
        "initial_state": "Trigger",
        "flags": {"allow_concurrent_calls": True},
    }


def main() -> None:
    """Write the flow definition and report what the checks say about it."""
    definition = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(definition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    questions = sum(
        1 for s in definition["states"] if s["type"] == "send-and-wait-for-reply"
    )
    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print(f"  {len(definition['states'])} widgets, {questions} questions")

    print("\nflow-check:")
    findings = check_flow(definition)
    if not findings:
        print("  all checks passed")
    for finding in findings:
        print(f"  [{finding.severity}] {finding.code}  {finding.summary}")
        for line in finding.detail[:6]:
            print(f"      {line}")

    if ENCRYPT_SERVICE_SID is None:
        print(
            "\nNOTE: Function SIDs are not set. Deploy encrypt_fields.js and\n"
            "publish_motherduck.js on this account, then fill in the SIDs at the\n"
            "top of this script before deploying the flow."
        )


if __name__ == "__main__":
    main()
