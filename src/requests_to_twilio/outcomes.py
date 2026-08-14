"""How a respondent's participation ended, and what that means for the data.

Two vocabularies, one derived from the other.

``outcome`` records **which terminal path the flow took** — six values, written
by the widget that ends the execution. It is the operational record: it says
what happened.

``final_status`` records **what the pipeline ended up with** — four values,
derived from ``outcome`` at the point every path converges. It is the analysis
record: it is the column a tracking view groups by.

Both live here rather than beside their producers because they had already
drifted once. The flows emitted ``unreachable``, ``undeliverable`` and
``optout`` long before the data checks listed them, so a round consisting
entirely of non-responders was reported as having *no recognisable outcome at
all* — the check blind to exactly the population it exists to count, and silent
about it. A vocabulary declared in three places is a vocabulary that will
disagree with itself; declared once, a seventh value is a change to this file
and every consumer sees it.
"""

from __future__ import annotations

#: Every value a flow may write to ``outcome``, in reporting order.
#:
#: ``optout`` is deliberately distinct from ``incomplete``. Someone who asked to
#: stop is exercising a right, not breaking off, and collapsing the two
#: overstates attrition while burying a consent signal.
OUTCOMES: tuple[str, ...] = (
    "complete",
    "declined",
    "incomplete",
    "unreachable",
    "undeliverable",
    "optout",
)

#: The analysis-ready rollup.
#:
#: ``declined`` keeps its own value rather than folding into ``incomplete``: a
#: refusal is a successful contact that produced a valid answer, and refusal
#: rate is reported separately from attrition in any ethics or IRB summary.
FINAL_STATUSES: tuple[str, ...] = (
    "complete",
    "declined",
    "incomplete",
    "failed",
)

#: ``outcome`` to ``final_status``.
#:
#: **``failed`` means the system let us down, never the respondent.** Only
#: ``undeliverable`` qualifies on the flow side - the first message never
#: arrived - joined at runtime by an encryption failure. A respondent who
#: refused, stopped or never replied did nothing wrong and is not a failure.
FINAL_STATUS_BY_OUTCOME: dict[str, str] = {
    "complete": "complete",
    "declined": "declined",
    "incomplete": "incomplete",
    "optout": "incomplete",
    "unreachable": "incomplete",
    "undeliverable": "failed",
}

#: What ``final_status`` becomes when encryption fails after the outcome is
#: already known. The row is still published - losing the answers because the
#: PII could not be sealed would be the worse trade - but it must not be counted
#: as a clean completion.
ENCRYPTION_FAILED_STATUS = "failed"

#: The value written when no branch matched. It should never appear; a column of
#: these means a terminal path was added without teaching this module about it,
#: which is the drift this file exists to make visible rather than silent.
UNKNOWN_STATUS = "unknown"


def final_status_for(outcome: str, encryption_ok: bool = True) -> str:
    """Return the analysis-ready status for one respondent.

    Args:
        outcome: The value the flow wrote to ``outcome``.
        encryption_ok: False when the encryption step failed for this row.

    Returns:
        One of :data:`FINAL_STATUSES`, or :data:`UNKNOWN_STATUS` for an outcome
        this module has not been taught.

    The flow computes this in Liquid rather than calling into here - Studio
    cannot import Python - so this function is what the tests compare that
    generated Liquid against. Two implementations of one rule is exactly the
    drift being guarded elsewhere in this file, so they are pinned to agree.

    """
    if not encryption_ok:
        return ENCRYPTION_FAILED_STATUS
    return FINAL_STATUS_BY_OUTCOME.get(outcome, UNKNOWN_STATUS)
