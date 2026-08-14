"""Tests for preloaded-data detection.

A `{{flow.data.x}}` reference that the launcher does not supply resolves to an
empty string. Nothing errors: the survey runs, respondents get "Hi ," and the
published column is blank. Because that is invisible until after the round,
these checks are the only thing standing between a typo and a ruined dataset.
"""

import pytest

from requests_to_twilio.flows import check_preloaded, preloaded_keys


def flow_with(*bodies: str) -> dict:
    """Build a minimal flow definition whose widgets carry the given text."""
    return {
        "states": [
            {"name": f"w{i}", "type": "send-message", "properties": {"body": b}}
            for i, b in enumerate(bodies)
        ]
    }


class TestPreloadedKeys:
    def test_finds_a_reference(self):
        assert preloaded_keys(flow_with("Hi {{flow.data.name}}")) == {"name"}

    def test_finds_several_across_widgets(self):
        definition = flow_with(
            "Hi {{flow.data.name}}", "Your case is {{flow.data.caseid}}"
        )
        assert preloaded_keys(definition) == {"name", "caseid"}

    def test_deduplicates(self):
        definition = flow_with("{{flow.data.name}}", "again {{flow.data.name}}")
        assert preloaded_keys(definition) == {"name"}

    def test_tolerates_whitespace(self):
        assert preloaded_keys(flow_with("{{ flow.data.name }}")) == {"name"}

    def test_ignores_widget_and_variable_references(self):
        """Only flow.data is preloaded; widgets and variables come from the run."""
        definition = flow_with(
            "{{widgets.q1.inbound.Body}} {{flow.variables.counter_error_1}}"
        )
        assert preloaded_keys(definition) == set()

    def test_finds_references_in_function_parameters(self):
        """Publish widgets reference preloads in parameters, not bodies."""
        definition = {
            "states": [
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "properties": {
                        "parameters": [
                            {"key": "caseid", "value": "{{flow.data.caseid}}"},
                            {"key": "arm", "value": "{{flow.data.treatment}}"},
                        ]
                    },
                }
            ]
        }
        assert preloaded_keys(definition) == {"caseid", "treatment"}

    def test_finds_references_in_split_conditions(self):
        definition = {
            "states": [
                {
                    "name": "split_arm",
                    "type": "split-based-on",
                    "properties": {"input": "{{flow.data.treatment}}"},
                }
            ]
        }
        assert preloaded_keys(definition) == {"treatment"}

    def test_empty_flow(self):
        assert preloaded_keys({"states": []}) == set()

    def test_survives_non_ascii(self):
        """Flow bodies carry Spanish and emoji; the scan must not choke."""
        definition = flow_with("¡Hola {{flow.data.name}}! 👋 ¿Cómo está?")
        assert preloaded_keys(definition) == {"name"}


class TestCheckPreloaded:
    def test_all_supplied(self):
        definition = flow_with("{{flow.data.name}} {{flow.data.caseid}}")
        missing, unused = check_preloaded(definition, {"name", "caseid"})
        assert missing == set()
        assert unused == set()

    def test_reports_missing(self):
        definition = flow_with("{{flow.data.name}} {{flow.data.caseid}}")
        missing, _ = check_preloaded(definition, {"name"})
        assert missing == {"caseid"}

    def test_reports_unused(self):
        missing, unused = check_preloaded(
            flow_with("{{flow.data.name}}"), {"name", "x"}
        )
        assert missing == set()
        assert unused == {"x"}

    def test_case_mismatch_is_a_mismatch(self):
        """`Name` and `name` are different keys - the usual silent failure."""
        missing, unused = check_preloaded(flow_with("{{flow.data.name}}"), {"Name"})
        assert missing == {"name"}
        assert unused == {"Name"}


@pytest.mark.parametrize(
    "reference",
    [
        "{{flow.data.p_number_original}}",
        "{{flow.data.niv_educativo_sisben}}",
        "{{flow.data.metr_area_name}}",
        "{{flow.data.codigo_d1}}",
    ],
)
def test_real_key_shapes_from_the_corpus(reference):
    """Keys observed in the account's own flows, including long snake_case."""
    assert len(preloaded_keys(flow_with(reference))) == 1
