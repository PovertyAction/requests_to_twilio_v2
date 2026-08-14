"""High-frequency checks on collected data.

`flows.check_flow` is the instrument-side equivalent: it verifies the survey was
*coded* correctly before a round. This module is the other half, run *during* a
round against what has actually arrived, and it exists because some defects are
only visible in the data - a respondent who answered twice looks fine in every
widget of the flow.

The checks here are deliberately about structure rather than substance. Whether
an answer is plausible is the analyst's question; whether the dataset holds one
observation per respondent, whether every row can be joined back to the sampling
frame, and whether break-offs are being recorded rather than lost, are questions
the pipeline itself has to answer.

**These findings are warnings, where the flow-side ones are errors.** That is
not timidity, it is the difference between the two halves. A flow check runs
*before* a round and can prevent the harm, so it blocks: refusing to deploy a
broken instrument costs nothing but a fix. A data check runs *after* the data
exists, and there is nothing left to prevent - the only useful thing it can do
is describe what arrived and let a person decide. A duplicate might be a defect
or might be a deliberate re-launch, and only the person running the round knows
which. Failing the command would also break any monitoring that runs it on a
loop while a round is live, which is precisely when it is most worth running.
"""

from __future__ import annotations

import pandas as pd

from .flows import Finding

#: The column that identifies a respondent. Named after the house convention -
#: `caseid` is the join key back to the sampling frame, and a row without one is
#: an orphan whatever else it contains.
DEFAULT_KEY = "caseid"

#: Outcome values this tooling's flows emit. Seeing none of them in a dataset
#: means the outcome column is missing, misnamed, or carries some other
#: vocabulary - and without one, completion cannot be told from break-off.
#:
#: `unreachable`, `undeliverable` and `optout` were emitted by the flows long
#: before they were listed here, which meant a round consisting entirely of
#: non-responders was reported as having no recognisable outcome at all.
RECOGNISED_OUTCOMES = (
    "complete",
    "declined",
    "incomplete",
    "unreachable",
    "undeliverable",
    "optout",
)


def duplicate_observations(
    frame: pd.DataFrame, key: str = DEFAULT_KEY
) -> dict[str, int]:
    """Return identifiers that appear more than once, with their counts.

    Args:
        frame: The collected dataset.
        key: The respondent identifier column.

    Returns:
        Identifier to row count, for identifiers appearing more than once.

    One respondent, one survey, one row. A duplicate means either the number was
    launched twice - a re-run without ``--resume`` will do it - or the flow let
    the respondent start a second execution themselves. Neither is visible from
    the flow definition, and neither announces itself: the duplicate rows look
    exactly like real ones.

    It matters more than a tidiness problem. A respondent with two rows is
    double-weighted in every mean, and if the two rows disagree there is no
    principled way to choose between them after the fact.

    """
    if key not in frame.columns or frame.empty:
        return {}

    ids = frame[key].astype("string").str.strip()
    counts = ids[ids.notna() & (ids != "")].value_counts()
    return {str(k): int(v) for k, v in counts[counts > 1].items()}


def unjoinable_rows(frame: pd.DataFrame, key: str = DEFAULT_KEY) -> int:
    """Count rows with no identifier, which cannot reach the sampling frame.

    Args:
        frame: The collected dataset.
        key: The respondent identifier column.

    Returns:
        The number of rows whose key is missing or blank.

    These are usually executions nobody launched - somebody messaging the number
    directly - so they carry no preloaded data at all. They are not
    recoverable: without the identifier there is nothing to match them to.

    """
    if key not in frame.columns:
        return len(frame)
    ids = frame[key].astype("string").str.strip()
    return int((ids.isna() | (ids == "")).sum())


def outcome_counts(frame: pd.DataFrame, column: str = "outcome") -> dict[str, int]:
    """Return how many rows ended in each outcome.

    Args:
        frame: The collected dataset.
        column: The final-status column.

    Returns:
        Outcome to row count, empty if the column is absent.

    """
    if column not in frame.columns or frame.empty:
        return {}
    values = frame[column].astype("string").fillna("(blank)").replace("", "(blank)")
    return {str(k): int(v) for k, v in values.value_counts().items()}


def check_dataset(frame: pd.DataFrame, key: str = DEFAULT_KEY) -> list[Finding]:
    """Run the data-side checks over a collected dataset.

    Args:
        frame: The collected dataset.
        key: The respondent identifier column.

    Returns:
        Findings, errors first. An empty list means every check passed.

    """
    findings: list[Finding] = []

    if frame.empty:
        return [Finding("warning", "no-data", "The dataset is empty")]

    duplicates = duplicate_observations(frame, key)
    if duplicates:
        total = sum(duplicates.values()) - len(duplicates)
        findings.append(
            Finding(
                "warning",
                "duplicate-observations",
                f"{len(duplicates)} respondent(s) have more than one row "
                f"({total} extra row(s))",
                [f"{k} appears {v} times" for k, v in list(duplicates.items())[:10]],
            )
        )

    orphans = unjoinable_rows(frame, key)
    if orphans:
        findings.append(
            Finding(
                "warning",
                "unjoinable-rows",
                f"{orphans} row(s) have no {key}, so they cannot be matched "
                "back to the sampling frame",
            )
        )

    outcomes = outcome_counts(frame)
    if outcomes and not any(o in outcomes for o in RECOGNISED_OUTCOMES):
        findings.append(
            Finding(
                "warning",
                "no-recognised-outcome",
                "No row carries a recognised outcome, so completion cannot be measured",
                [f"{k}: {v}" for k, v in outcomes.items()][:10],
            )
        )

    return findings
