"""Tests for the outcome vocabulary and its rollup.

The vocabulary lived in three places - the builder's widgets, the data checks,
and the spec's closings - and had already drifted: the flows emitted
`unreachable`, `undeliverable` and `optout` long before `RECOGNISED_OUTCOMES`
listed them, so a round of pure non-response was reported as having no
recognisable outcome at all.

These tests exist to keep it in one place, and to keep the Liquid the flow runs
agreeing with the Python everything else runs.
"""

import re

import pytest

from requests_to_twilio.hfc import RECOGNISED_OUTCOMES
from requests_to_twilio.outcomes import (
    FINAL_STATUS_BY_OUTCOME,
    FINAL_STATUS_NOTES,
    FINAL_STATUSES,
    OUTCOMES,
    UNKNOWN_STATUS,
    final_status_for,
    final_status_liquid,
)


class TestTheVocabularyIsDeclaredOnce:
    def test_the_data_checks_use_this_list(self):
        """Not a copy of it - the copy is what drifted."""
        assert RECOGNISED_OUTCOMES is OUTCOMES

    def test_every_outcome_maps_to_a_final_status(self):
        """A new outcome with no mapping would silently become `unknown`."""
        assert set(FINAL_STATUS_BY_OUTCOME) == set(OUTCOMES)

    def test_every_mapping_target_is_a_real_final_status(self):
        assert set(FINAL_STATUS_BY_OUTCOME.values()) <= set(FINAL_STATUSES)

    def test_every_final_status_is_documented(self):
        """The column an analysis groups by cannot be the undocumented one."""
        assert set(FINAL_STATUS_NOTES) == set(FINAL_STATUSES)


class TestWhatFailedMeans:
    """System-side only. A respondent is never a failure."""

    @pytest.mark.parametrize("outcome", ["declined", "optout", "unreachable"])
    def test_a_respondents_choice_is_not_a_failure(self, outcome):
        assert final_status_for(outcome) != "failed"

    def test_a_refusal_keeps_its_own_value(self):
        """Refusal rate is reported separately from attrition."""
        assert final_status_for("declined") == "declined"

    def test_opting_out_is_incomplete_not_declined(self):
        """They consented and then stopped - a different thing from refusing."""
        assert final_status_for("optout") == "incomplete"

    def test_undeliverable_is_the_only_flow_side_failure(self):
        failures = [o for o in OUTCOMES if final_status_for(o) == "failed"]
        assert failures == ["undeliverable"]

    def test_encryption_failure_beats_a_clean_outcome(self):
        """The row is published, but must not count as a clean completion."""
        assert final_status_for("complete", encryption_ok=False) == "failed"

    def test_an_unknown_outcome_is_visible_rather_than_guessed(self):
        assert final_status_for("something_new") == UNKNOWN_STATUS


class TestTheLiquidAgreesWithThePython:
    """Two implementations of one rule, pinned together.

    Studio cannot import Python, so the flow computes this in Liquid. That makes
    it the exact kind of second implementation this module exists to prevent
    elsewhere - so it is generated from the same mapping, and compared here.
    """

    def arms(self):
        """Return the {outcome: status} pairs the generated Liquid encodes."""
        found = re.findall(r"\{% when '([^']+)' %\}([a-z_]+)", final_status_liquid())
        return dict(found)

    def test_the_liquid_encodes_exactly_the_mapping(self):
        assert self.arms() == FINAL_STATUS_BY_OUTCOME

    def test_every_outcome_has_an_arm(self):
        assert set(self.arms()) == set(OUTCOMES)

    def test_an_unmatched_outcome_falls_through_to_unknown(self):
        """Rather than to an empty string, which reads as a missing column."""
        assert f"{{% else %}}{UNKNOWN_STATUS}" in final_status_liquid()

    def test_it_reads_the_variable_it_is_told_to(self):
        assert "flow.variables.outcome" in final_status_liquid()
        assert "flow.variables.other" in final_status_liquid("other")
