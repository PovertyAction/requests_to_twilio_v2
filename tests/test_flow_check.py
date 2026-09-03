"""Tests for the flow checks - the high-frequency-check equivalent.

These verify the instrument was coded correctly, not that the data looks right.
Every check here corresponds to a defect that is invisible in the Studio editor
and only surfaces as unusable data after a round.
"""

import json
from pathlib import Path

from requests_to_twilio.flows import check_flow, published_columns, unpaired_answers


def question(name, **transitions):
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": {},
        "transitions": [{"event": e, "next": d} for e, d in transitions.items()],
    }


def publish(*params):
    return {
        "name": "publish_gsheets",
        "type": "run-function",
        "properties": {"parameters": [{"key": k, "value": v} for k, v in params]},
        "transitions": [],
    }


def widget(definition: dict, name: str) -> dict:
    """Address a widget by name. The fixture's order is not a contract."""
    return next(s for s in definition["states"] if s["name"] == name)


def codes(definition) -> set[str]:
    return {f.code for f in check_flow(definition)}


def healthy_flow() -> dict:
    """Build a minimal flow that passes every check."""
    return {
        "states": [
            question(
                "q1",
                reply="stopcheck_q1",
                timeout="mark_no_reply",
                deliveryFailure="mark_fail",
            ),
            # A healthy survey flow lets somebody stop. Inside a WhatsApp
            # session a "STOP" is an ordinary inbound message, so it is only
            # honoured if the flow looks for it.
            {
                "name": "stopcheck_q1",
                "type": "split-based-on",
                "transitions": [
                    {
                        "event": "match",
                        "next": "mark_optout",
                        "conditions": [
                            {
                                "friendly_name": "asked to stop",
                                "type": "regex",
                                "arguments": ["{{widgets.q1.inbound.Body}}"],
                                "value": "(?:\\s*(?:stop|quit|unsubscribe)\\s*)",
                            }
                        ],
                    },
                    {"event": "noMatch", "next": "publish_gsheets"},
                ],
            },
            {
                "name": "mark_optout",
                "type": "set-variables",
                "transitions": [{"event": "next", "next": "publish_gsheets"}],
            },
            {
                "name": "mark_no_reply",
                "type": "set-variables",
                "transitions": [{"event": "next", "next": "publish_gsheets"}],
            },
            {
                "name": "mark_fail",
                "type": "set-variables",
                "transitions": [{"event": "next", "next": "publish_gsheets"}],
            },
            {
                "name": "function_encrypt",
                "type": "run-function",
                "properties": {"parameters": []},
                "transitions": [],
            },
            publish(
                ("q1", "{{widgets.q1.inbound.Body}}"),
                ("q1_status", "{{flow.variables.q1_status}}"),
                ("set_complete", "{{flow.variables.set_complete}}"),
                # A flow named "healthy" should be exemplary, not merely
                # passing. Without this it earns `no-derived-final-status`,
                # which is correct: the `set_*` flags are `1` or blank, never
                # `0`, so a single flag cannot say how the survey ended.
                ("final_status", "{{flow.variables.final_status}}"),
            ),
        ]
    }


def test_healthy_flow_passes_everything():
    assert check_flow(healthy_flow()) == []


def test_missing_timeout_is_an_error():
    definition = healthy_flow()
    widget(definition, "q1")["transitions"] = [
        {"event": "reply", "next": "publish_gsheets"},
        {"event": "deliveryFailure", "next": "mark_fail"},
    ]
    assert "unhandled-timeout" in codes(definition)


def test_missing_delivery_failure_is_an_error():
    definition = healthy_flow()
    widget(definition, "q1")["transitions"] = [
        {"event": "reply", "next": "publish_gsheets"},
        {"event": "timeout", "next": "mark_no_reply"},
    ]
    assert "unhandled-delivery-failure" in codes(definition)


def test_break_off_that_never_publishes_is_an_error():
    definition = healthy_flow()
    widget(definition, "mark_no_reply")["transitions"] = []
    assert "unpublished-paths" in codes(definition)


def test_publish_without_final_status_is_an_error():
    """A row that cannot say how the survey ended cannot support a response rate."""
    definition = healthy_flow()
    definition["states"][-1] = publish(("q1", "{{widgets.q1.inbound.Body}}"))
    assert "no-final-status" in codes(definition)


def test_section_scoped_status_counts_as_final_status():
    """set_no_reply_dem is the house convention, not just set_complete."""
    definition = healthy_flow()
    definition["states"][-1] = publish(
        ("q1", "{{widgets.q1.inbound.Body}}"),
        ("q1_status", "{{flow.variables.q1_status}}"),
        ("set_no_reply_dem", "{{flow.variables.set_no_reply_dem}}"),
    )
    assert "no-final-status" not in codes(definition)


def test_derived_final_status_alone_satisfies_the_requirement():
    """The documented convention must not fail an error-severity check.

    `final_status` was missing from the accepted set, so a flow built the way
    this project's own docs prescribe - derive `final_status` at the convergence
    point, drop the legacy paradata - errored and could not be deployed. The
    committed demo hid it by publishing both vocabularies at once, so its pass
    was earned entirely by the `set_*` columns and said nothing about this one.
    """
    definition = healthy_flow()
    definition["states"][-1] = publish(
        ("q1", "{{widgets.q1.inbound.Body}}"),
        ("final_status", "{{flow.variables.final_status}}"),
    )
    found = codes(definition)
    assert "no-final-status" not in found
    # And no nudge, because the recommended column is the one that is there.
    assert "no-derived-final-status" not in found


def test_legacy_flags_alone_earn_a_recommendation_not_an_error():
    """Strongly suggest `final_status`; never demand a particular vocabulary.

    How an outcome is composed from somebody's own `set_` flags is their
    business. What is worth saying every run is that the flags are `1` or
    *blank* - never `0` - so "not complete" is encoded as absence and reads the
    same as a dropped column or a shifted header.
    """
    definition = healthy_flow()
    definition["states"][-1] = publish(
        ("q1", "{{widgets.q1.inbound.Body}}"),
        ("set_complete", "{{flow.variables.set_complete}}"),
    )
    found = codes(definition)
    assert "no-final-status" not in found
    assert "no-derived-final-status" in found


def test_a_consent_flag_is_not_an_outcome_on_its_own():
    """`set_consent` says they agreed to start, never how it ended.

    It stays in the accepted set - policing flag vocabularies is not this
    checker's job - so the row does not error. It does earn the recommendation,
    which is what stops a flow recording only consent from passing in silence.
    """
    definition = healthy_flow()
    definition["states"][-1] = publish(
        ("q1", "{{widgets.q1.inbound.Body}}"),
        ("set_consent", "{{flow.variables.set_consent}}"),
    )
    assert "no-derived-final-status" in codes(definition)


def test_the_committed_demo_publishes_the_recommended_column():
    """Pins the fix in the artifact, not only in the unit.

    The demo publishes 45 parameters carrying both vocabularies, so
    `no-final-status` passing on it proves nothing by itself.
    """
    definition = json.loads(
        (
            Path(__file__).resolve().parents[1] / "flows" / "data_use_demo_en.json"
        ).read_text(encoding="utf-8")
    )
    assert "no-derived-final-status" not in codes(definition)


def test_split_without_nomatch_is_a_warning():
    definition = healthy_flow()
    definition["states"].append(
        {
            "name": "split_q1",
            "type": "split-based-on",
            "properties": {"input": "{{widgets.q1.inbound.Body}}"},
            "transitions": [{"event": "match", "next": "publish_gsheets"}],
        }
    )
    findings = {f.code: f.severity for f in check_flow(definition)}
    assert findings.get("split-without-nomatch") == "warning"


def test_publishing_without_encryption_is_a_warning():
    definition = healthy_flow()
    # Drop the encrypt widget: publishing identifiers in clear is the risk.
    definition["states"] = [
        s for s in definition["states"] if s["name"] != "function_encrypt"
    ]
    findings = {f.code: f.severity for f in check_flow(definition)}
    assert findings.get("no-encryption") == "warning"


def test_credentials_in_definition_are_an_error():
    # Assembled at runtime so the literal PEM header never appears in this
    # file: the detect-private-key pre-commit hook scans test files too, and a
    # fixture that trips it would block every commit in the repository.
    fake_pem = (
        "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQ...\n-----END " + "PRIVATE KEY-----"
    )
    definition = healthy_flow()
    widget(definition, "q1")["properties"]["body"] = fake_pem
    assert "credentials" in codes(definition)


def test_a_flow_that_ignores_stop_is_a_warning():
    """Twilio's opt-out handling does not reach inside a WhatsApp session.

    A "STOP" arrives as an ordinary inbound message, so unless the flow looks
    for it the word is stored as the answer and the next question is sent
    anyway. Being asked three more questions after saying stop is a
    research-ethics problem before it is a bug.
    """
    definition = healthy_flow()
    definition["states"] = [
        s for s in definition["states"] if s["name"] != "stopcheck_q1"
    ]
    widget(definition, "q1")["transitions"] = [
        {"event": "reply", "next": "publish_gsheets"},
        {"event": "timeout", "next": "mark_no_reply"},
        {"event": "deliveryFailure", "next": "mark_fail"},
    ]
    findings = {f.code: f.severity for f in check_flow(definition)}
    assert findings.get("no-optout-path") == "warning"


def test_a_flow_with_its_own_stop_wording_still_passes():
    """The check asks whether the flow considers the question, not how."""
    definition = healthy_flow()
    condition = widget(definition, "stopcheck_q1")["transitions"][0]["conditions"][0]
    condition["value"] = "(?:\\s*(?:cancelar|salir)\\s*)"
    assert "no-optout-path" not in codes(definition)


def test_errors_sort_before_warnings():
    definition = healthy_flow()
    widget(definition, "mark_no_reply")["transitions"] = []
    severities = [f.severity for f in check_flow(definition)]
    assert severities == sorted(severities, key=lambda s: s != "error")


class TestPublishedColumns:
    def test_classifies_each_source(self):
        definition = {
            "states": [
                publish(
                    ("caseid", "{{flow.data.caseid}}"),
                    ("q1", "{{widgets.q1.inbound.Body}}"),
                    ("set_complete", "{{flow.variables.set_complete}}"),
                    ("enc_name", "{{widgets.function_encrypt.parsed.enc_name}}"),
                )
            ]
        }
        assert published_columns(definition) == [
            ("caseid", "preload"),
            ("q1", "answer"),
            ("set_complete", "variable"),
            ("enc_name", "encrypted"),
        ]

    def test_no_publish_widget(self):
        assert published_columns({"states": []}) == []


class TestUnpairedAnswers:
    def test_suffix_pairing_is_recognised(self):
        definition = {
            "states": [
                publish(
                    ("q1", "{{widgets.q1.inbound.Body}}"),
                    ("q1_status", "{{flow.variables.q1_status}}"),
                )
            ]
        }
        assert unpaired_answers(definition) == []

    def test_answer_with_no_status_is_reported(self):
        definition = {
            "states": [
                publish(
                    ("q1", "{{widgets.q1.inbound.Body}}"),
                    ("q2", "{{widgets.q2.inbound.Body}}"),
                )
            ]
        }
        assert unpaired_answers(definition) == ["q1", "q2"]

    def test_variables_are_not_expected_to_pair(self):
        definition = {
            "states": [publish(("set_complete", "{{flow.variables.set_complete}}"))]
        }
        assert unpaired_answers(definition) == []
