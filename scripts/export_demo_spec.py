"""Export the demo instrument's language tables as a survey spec.

A one-off. The demo flow's strings currently live as two Python dicts in
``build_data_use_demo.py``, which works but means the instrument can only be read
or changed by somebody who reads Python and has this repository checked out. This
lifts them into ``surveys/data_use_demo.json`` - the spec format - without
touching the builder, so nothing the August round depends on changes.

Run it once, review the JSON, and from then on the JSON is the thing that gets
edited. The builder keeps reading its dicts until the compiler is switched over,
and the pinning test in ``tests/test_demo_builder.py`` guarantees the flow it
emits does not move in the meantime.

The point of doing it this way round - export first, compile later - is that the
export can be checked against something that already exists. A spec written by
hand would be a new claim about what the instrument is; a spec derived from the
tables that build the running flow is a restatement of it, and
``check_spec`` plus the round-trip test say whether the restatement is faithful.

Run with `uv run python scripts/export_demo_spec.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_data_use_demo as demo  # noqa: E402

from requests_to_twilio.log import configure_output_encoding  # noqa: E402
from requests_to_twilio.spec import (  # noqa: E402
    ChoiceRow,
    MessageRow,
    Settings,
    Spec,
    SurveyRow,
    check_spec,
    review_notes,
    save_spec,
)

SURVEY_DIR = REPO_ROOT / "surveys"
FORM_ID = "data_use_demo"

#: The two arms, as groups. ARM 1 asks in dense prose and accepts anything; ARM
#: 2 asks the same four constructs as tappable lists. The asymmetry is the
#: experiment, not an oversight, so it has to survive the export intact.
ARMS = (
    ("ARM1", "1", "ARM 1 - dense prose, typed answers, no validation"),
    ("ARM2", "2", "ARM 2 - plain language, tappable lists, validated"),
)

#: Which closing each outcome gets, and where its text comes from. Four of the
#: five outcomes are reachable in session and carry their own body; the fifth
#: never opened the 24-hour window and can only be reached by an approved
#: template. `undeliverable` is absent on purpose: a number that could not
#: receive the first message cannot receive this one either.
CLOSINGS = (
    ("close_complete", "complete", "close_complete"),
    ("close_declined", "declined", "close_declined"),
    ("close_incomplete", "incomplete", "close_incomplete"),
    ("close_optout", "optout", "stop_ack"),
)

#: Boilerplate with no position in the graph. `stop_ack` is deliberately not
#: here - it is the body of the opt-out closing, so it is a positioned message
#: and lives in the survey sheet as a `close` row.
MESSAGE_KEYS = (
    "error_numeric",
    "error_option",
    "error_option_labels",
    "unsolicited",
)


def _text(getter) -> dict[str, str]:
    """Collect one string across every language into a translatable value."""
    return {lang: getter(demo.LANGS[lang]) for lang in sorted(demo.LANGS)}


def build_choices() -> list[ChoiceRow]:
    """Every option list the instrument uses, consent included.

    Consent is a choice list like any other. That is the point: its wording is a
    question's wording and belongs where an RA reads it, next to the four it
    precedes, rather than buried in a settings block as boilerplate.
    """
    rows: list[ChoiceRow] = [
        ChoiceRow(
            list_name="consent",
            value="yes",
            # Matches the quick-reply action id the generated template carries,
            # because a tapped button's payload is what arrives.
            option_id="consent_yes",
            label=_text(lambda t: t["consent"]["button_yes"]),
            typed=_text(lambda t: t["consent"]["typed_yes"]),
        ),
        ChoiceRow(
            list_name="consent",
            value="no",
            option_id="consent_no",
            label=_text(lambda t: t["consent"]["button_no"]),
            typed=_text(lambda t: t["consent"]["typed_no"]),
        ),
    ]

    for key in demo.QUESTION_KEYS:
        list_name = key.lower()
        english = demo.EN["arm2"][key]["options"]
        for index in range(len(english)):
            option = english[index]
            rows.append(
                ChoiceRow(
                    list_name=list_name,
                    # The code the warehouse stores. `option_code` is what the
                    # flow already writes, so taking it from there rather than
                    # from the position keeps an explicit code - a "Prefer not
                    # to say" at -99 - explicit.
                    value=demo.option_code(option, index + 1),
                    option_id=option[0],
                    label=_text(
                        lambda t, k=key, i=index: t["arm2"][k]["options"][i][1]
                    ),
                    description=_text(
                        lambda t, k=key, i=index: t["arm2"][k]["options"][i][2]
                    ),
                )
            )
    return rows


def build_survey() -> list[SurveyRow]:
    """Lay out the instrument in flow order: opener, consent, arms, closings."""
    rows: list[SurveyRow] = [
        # The only business-initiated message, so the only one Meta reviews. Its
        # copy lives in templates/<name>.json and not here: the widget carries a
        # SID and no text, so there is nothing for the two to disagree about.
        SurveyRow(
            type="template",
            name="intro",
            role="intro",
            template=_text(lambda t: t["intro_template"]),
            retries=0,
            stop_check=False,
            publish=False,
        ),
        SurveyRow(
            type="select_button consent",
            name="consent",
            role="consent",
            label=_text(lambda t: t["consent"]["body"]),
            # One re-ask, not two. A second unreadable reply is a break-off
            # rather than a decision, and is recorded as `unclear` - not as a
            # refusal, which is a number the study reports.
            retries=1,
            stop_check=False,
        ),
    ]

    for group, arm, purpose in ARMS:
        rows.append(
            SurveyRow(
                type="begin group",
                name=group,
                role="",
                # The arm is preloaded from the sample file, so randomisation
                # happens offline where balance can be checked before anyone is
                # contacted.
                relevance=f"${{arm}}='{arm}'",
                label={lang: purpose for lang in sorted(demo.LANGS)},
            )
        )
        for key in demo.QUESTION_KEYS:
            if group == "ARM1":
                rows.append(
                    SurveyRow(
                        type="text",
                        name=key,
                        label=_text(lambda t, k=key: t["arm1"][k]),
                        # Deliberately none. Validating an open answer would turn
                        # ARM 1 into ARM 2 and destroy the comparison.
                        retries=0,
                    )
                )
            else:
                rows.append(
                    SurveyRow(
                        type=f"select_list {key.lower()}",
                        name=key,
                        label=_text(lambda t, k=key: t["arm2"][k]["body"]),
                        retries=2,
                        list_button=_text(lambda t: t["arm2"]["button"]),
                    )
                )
        rows.append(SurveyRow(type="end group", name=group))

    for name, outcome, source in CLOSINGS:
        rows.append(
            SurveyRow(
                type="note",
                name=name,
                role="close",
                relevance=f"${{outcome}}='{outcome}'",
                label=_text(lambda t, s=source: t[s]),
                publish=False,
            )
        )

    rows.append(
        SurveyRow(
            type="note",
            name="close_never_started",
            role="close",
            # Never replied, so the window never opened and only an approved
            # template can reach them at all.
            relevance="${outcome}='unreachable'",
            template=_text(lambda t: t["close_template"]),
            publish=False,
        )
    )
    rows.append(
        SurveyRow(
            type="note",
            name="unsolicited_reply",
            role="unsolicited",
            label=_text(lambda t: t["unsolicited"]),
            publish=False,
        )
    )
    return rows


def build_spec() -> Spec:
    """Assemble the whole spec from the builder's language tables."""
    languages = sorted(demo.LANGS)
    return Spec(
        settings=Settings(
            form_id=FORM_ID,
            form_title="Data use demo (ARM1/ARM2 experiment)",
            languages=languages,
            default_language="en",
            functions_service=demo.FUNCTIONS_SERVICE_NAME,
            default_timeout=int(demo.TIMEOUT),
            default_retries=2,
            preloads=["caseid", "name", "arm", "sent_at"],
            flow_name={
                lang: f"{FORM_ID}_{demo.LANGS[lang]['flow_suffix']}"
                for lang in languages
            },
            description=_text(lambda t: t["description"]),
        ),
        survey=build_survey(),
        choices=build_choices(),
        messages=[
            MessageRow(key=key, text=_text(lambda t, k=key: t[k]))
            for key in MESSAGE_KEYS
        ]
        + [
            MessageRow(key="list_button", text=_text(lambda t: t["arm2"]["button"])),
            # Comma-delimited, and safe here because a stop word is a single
            # token. Option labels use pipes for the same job precisely because
            # they do contain commas.
            MessageRow(
                key="stop_words", text=_text(lambda t: ", ".join(t["stop_words"]))
            ),
        ],
    )


def main() -> None:
    """Write the spec, then say whether it is usable and what needs reading."""
    # This script prints consent wording and question bodies, which carry emoji.
    configure_output_encoding()
    spec = build_spec()
    path = save_spec(spec, SURVEY_DIR / f"{FORM_ID}.json")
    print(f"spec      {path.relative_to(REPO_ROOT).as_posix()}")

    questions = spec.questions()
    print(
        f"          {len(spec.survey)} rows, {len(questions)} questions, "
        f"{len(spec.choices)} options, "
        f"{spec.total_widget_count()} widgets before the spine"
    )
    for row in questions:
        if row.is_question:
            print(
                f"            {row.name:20} {row.type:26} {spec.widget_count(row)} widgets"
            )

    problems = check_spec(spec)
    if problems:
        print("\n  check     the spec is not usable:")
        for problem in problems:
            print(f"              {problem}")
    else:
        print("  check     all checks passed")

    for note in review_notes(spec):
        print(f"\n  review    {note}")

    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
