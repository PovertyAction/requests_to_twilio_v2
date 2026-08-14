"""Tests for the data-side high-frequency checks.

These catch defects that do not exist in the instrument. A flow can be perfectly
coded - every check in `flows.check_flow` green - and still produce a dataset
with two rows for one respondent, because the duplicate came from how the round
was run rather than from how the survey was built.
"""

import pandas as pd

from requests_to_twilio.hfc import (
    check_dataset,
    duplicate_observations,
    outcome_counts,
    unjoinable_rows,
)


def frame(rows):
    return pd.DataFrame(rows, dtype="object")


def codes(frame_):
    return {f.code for f in check_dataset(frame_)}


class TestDuplicateObservations:
    """One respondent, one survey, one row."""

    def test_a_clean_dataset_has_none(self):
        data = frame([{"caseid": "A"}, {"caseid": "B"}])
        assert duplicate_observations(data) == {}

    def test_a_repeated_caseid_is_reported_with_its_count(self):
        data = frame([{"caseid": "A"}, {"caseid": "A"}, {"caseid": "B"}])
        assert duplicate_observations(data) == {"A": 2}

    def test_whitespace_does_not_hide_a_duplicate(self):
        """'A' and ' A ' are the same respondent, launched twice."""
        data = frame([{"caseid": "A"}, {"caseid": " A "}])
        assert duplicate_observations(data) == {"A": 2}

    def test_blanks_are_not_counted_as_duplicates_of_each_other(self):
        """Two unjoinable rows are a different problem, reported separately."""
        data = frame([{"caseid": ""}, {"caseid": None}, {"caseid": "A"}])
        assert duplicate_observations(data) == {}

    def test_a_duplicate_is_a_warning_not_an_error(self):
        """Data that already exists cannot be blocked, only described.

        A duplicate may equally be a deliberate re-launch, and only the person
        running the round can tell which.
        """
        data = frame([{"caseid": "A"}, {"caseid": "A"}])
        finding = next(
            f for f in check_dataset(data) if f.code == "duplicate-observations"
        )
        assert finding.severity == "warning"
        assert "A appears 2 times" in finding.detail

    def test_the_summary_counts_extra_rows_not_respondents(self):
        """Three rows for one respondent is two rows too many, not three."""
        data = frame([{"caseid": "A"}] * 3)
        finding = next(
            f for f in check_dataset(data) if f.code == "duplicate-observations"
        )
        assert "2 extra row(s)" in finding.summary

    def test_a_missing_key_column_reports_nothing(self):
        assert duplicate_observations(frame([{"other": "x"}])) == {}


class TestUnjoinableRows:
    def test_counts_blank_and_missing_identifiers(self):
        data = frame([{"caseid": "A"}, {"caseid": ""}, {"caseid": None}])
        assert unjoinable_rows(data) == 2

    def test_every_row_is_unjoinable_without_the_column(self):
        assert unjoinable_rows(frame([{"other": "x"}, {"other": "y"}])) == 2

    def test_it_is_reported(self):
        """Without the identifier there is nothing to match the row to."""
        data = frame([{"caseid": "A"}, {"caseid": None}])
        assert "unjoinable-rows" in codes(data)


class TestOutcomes:
    def test_counts_each_outcome(self):
        data = frame(
            [{"outcome": "complete"}, {"outcome": "complete"}, {"outcome": "declined"}]
        )
        assert outcome_counts(data) == {"complete": 2, "declined": 1}

    def test_a_recognised_outcome_passes(self):
        data = frame([{"caseid": "A", "outcome": "complete"}])
        assert "no-recognised-outcome" not in codes(data)

    def test_nothing_recognisable_is_a_warning(self):
        """Completion cannot be measured from outcomes nobody defined."""
        data = frame([{"caseid": "A", "outcome": "banana"}])
        assert "no-recognised-outcome" in codes(data)

    def test_a_missing_outcome_column_says_nothing(self):
        assert outcome_counts(frame([{"caseid": "A"}])) == {}


class TestCheckDataset:
    def test_a_healthy_dataset_passes_everything(self):
        data = frame(
            [
                {"caseid": "A", "outcome": "complete"},
                {"caseid": "B", "outcome": "incomplete"},
            ]
        )
        assert check_dataset(data) == []

    def test_an_empty_dataset_is_a_warning_not_a_crash(self):
        assert codes(pd.DataFrame()) == {"no-data"}

    def test_both_problems_are_reported_together(self):
        """A round can be wrong in more than one way at once."""
        data = frame(
            [
                {"caseid": "A", "outcome": "complete"},
                {"caseid": "A", "outcome": "complete"},
                {"caseid": None, "outcome": "undeliverable"},
            ]
        )
        assert {"duplicate-observations", "unjoinable-rows"} <= codes(data)


class TestSeverity:
    """Data checks describe; flow checks prevent.

    A flow check runs before a round and refusing to deploy costs only a fix. A
    data check runs after the data exists, so blocking achieves nothing - and it
    is meant to run on a loop while a round is live, which a failure would
    break.
    """

    def test_nothing_here_blocks_by_default(self):
        data = frame(
            [
                {"caseid": "A", "outcome": "complete"},
                {"caseid": "A", "outcome": "complete"},
                {"caseid": None, "outcome": "undeliverable"},
            ]
        )
        assert all(f.severity == "warning" for f in check_dataset(data))
