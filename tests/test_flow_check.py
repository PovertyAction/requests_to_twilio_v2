"""Tests for the flow checks - the high-frequency-check equivalent.

These verify the instrument was coded correctly, not that the data looks right.
Every check here corresponds to a defect that is invisible in the Studio editor
and only surfaces as unusable data after a round.
"""

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


def codes(definition) -> set[str]:
    return {f.code for f in check_flow(definition)}


def healthy_flow() -> dict:
    """Build a minimal flow that passes every check."""
    return {
        "states": [
            question(
                "q1",
                reply="publish_gsheets",
                timeout="mark_no_reply",
                deliveryFailure="mark_fail",
            ),
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
            ),
        ]
    }


def test_healthy_flow_passes_everything():
    assert check_flow(healthy_flow()) == []


def test_missing_timeout_is_an_error():
    definition = healthy_flow()
    definition["states"][0]["transitions"] = [
        {"event": "reply", "next": "publish_gsheets"},
        {"event": "deliveryFailure", "next": "mark_fail"},
    ]
    assert "unhandled-timeout" in codes(definition)


def test_missing_delivery_failure_is_an_error():
    definition = healthy_flow()
    definition["states"][0]["transitions"] = [
        {"event": "reply", "next": "publish_gsheets"},
        {"event": "timeout", "next": "mark_no_reply"},
    ]
    assert "unhandled-delivery-failure" in codes(definition)


def test_break_off_that_never_publishes_is_an_error():
    definition = healthy_flow()
    definition["states"][1]["transitions"] = []  # mark_no_reply goes nowhere
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
    definition["states"][0]["properties"]["body"] = fake_pem
    assert "credentials" in codes(definition)


def test_errors_sort_before_warnings():
    definition = healthy_flow()
    definition["states"][1]["transitions"] = []
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
