"""The flows this repository ships must satisfy what it prescribes.

`rtt flow check` is the repository's central claim: run this before every round.
Nothing ran it on the repository's own worked example. The only guard was the
builder-pinning test, which asserts the committed JSON matches what the builder
emits - so a builder change that introduced a check failure would be reproduced
faithfully into the JSON and pass CI green.

These tests close that. They also pin the counter-example, because a
counter-example is only useful while it keeps failing.
"""

import json
import re
from pathlib import Path

import pytest

from requests_to_twilio.flows import (
    ACCOUNT_ONLY_CHECKS,
    TOTAL_CHECKS,
    check_flow,
)

ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / "flows"

#: The flows a stranger is told to deploy. Both must be clean.
EXEMPLARY = ("data_use_demo_en.json", "data_use_demo_es.json")

#: Kept unmodified as provenance, and referenced in README.md as a flow that
#: reports real defects. Its findings are the point.
COUNTER_EXAMPLE = "foro_nacional_datos_source.json"


def definition(name: str) -> dict:
    return json.loads((FLOWS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", EXEMPLARY)
def test_the_shipped_demo_passes_every_check_it_can_run_offline(name):
    """Not one error, and not one warning either.

    Warnings count here in a way they do not for somebody else's flow. This is
    the file `START-HERE.md` tells a reader to deploy, and the pattern anybody
    copying this repository will start from - so a warning left in it is a
    warning propagated by example.
    """
    findings = check_flow(definition(name))
    assert findings == [], [f"{f.severity} {f.code}" for f in findings]


@pytest.mark.parametrize("name", EXEMPLARY)
def test_the_shipped_demo_publishes_a_derived_final_status(name):
    """The convention, present in the artifact that demonstrates it."""
    codes = {f.code for f in check_flow(definition(name))}
    assert "no-derived-final-status" not in codes


def test_the_counter_example_still_fails():
    """`README.md` promises this file reports real defects. Hold it to that.

    A pulled production flow, kept as provenance. If a future change to the
    checker silenced these, the README would be making a claim the repository
    no longer demonstrates - and the more likely direction of that mistake is
    the checker getting quieter, not the flow getting better.
    """
    findings = check_flow(definition(COUNTER_EXAMPLE))
    codes = {f.code for f in findings}

    assert any(f.severity == "error" for f in findings)
    # The four it has always reported, each a distinct class of defect.
    assert "unmatchable-condition" in codes
    assert "no-encryption" in codes
    assert "no-optout-path" in codes
    assert "unpaired-answers" in codes


def test_total_checks_matches_the_source():
    """Keep the count the CLI prints honest.

    The CLI tells the reader "4 of 22 checks" did not run. That number is a
    constant, and a constant beside a growing list is a number that drifts - so
    it is counted from the source rather than trusted.
    """
    source = (ROOT / "src" / "requests_to_twilio" / "flows.py").read_text(
        encoding="utf-8"
    )
    emitted = set(re.findall(r'Finding\(\s*"[a-z]+",\s*"([a-z-]+)"', source))
    assert len(emitted) == TOTAL_CHECKS, sorted(emitted)
    assert set(ACCOUNT_ONLY_CHECKS) <= emitted


def test_the_skill_lists_every_check_the_code_emits():
    """The agent-facing table drifted to 15 of 22, missing two errors.

    A reader working from the skill could hit a non-zero exit on a code it never
    named - and two of the six missing were error severity, so they gate a
    deploy. `docs/running-a-round.md` had all of them; the skill did not, and
    nothing compared the two.
    """
    skill = (ROOT / ".claude" / "skills" / "studio-flow" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "src" / "requests_to_twilio" / "flows.py").read_text(
        encoding="utf-8"
    )
    emitted = set(re.findall(r'Finding\(\s*"[a-z]+",\s*"([a-z-]+)"', source))
    listed = set(re.findall(r"^\| `([a-z-]+)` \| (?:error|warning) \|", skill, re.M))
    assert emitted - listed == set(), sorted(emitted - listed)
    assert listed - emitted == set(), sorted(listed - emitted)
