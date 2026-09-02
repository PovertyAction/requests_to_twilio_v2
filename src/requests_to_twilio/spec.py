"""The survey spec: an XLSForm-shaped description of a WhatsApp instrument.

A Studio flow is not reviewable. The demo instrument is 73 widgets for 8
questions, and the per-question pattern - ask, stop-check, validate, store,
count the retry, decide, nudge, give up - is eight widgets repeated verbatim.
Nobody reads that canvas, which means nobody checks the instrument.

So the instrument is described here instead, as rows, in the shape every RA at
IPA already knows from SurveyCTO. One row is one question *including* its whole
subgraph: ``select_list p1`` with ``retries: 2`` is those eight widgets, and a
plain ``text`` question is three.

This describes a SURVEY, and only a survey
------------------------------------------
The spine the compiler wraps these rows in is a survey's spine, and it assumes
things that are true of a survey and false of most other flows:

* **Consent is asked, once, before any question.** :func:`check_spec` refuses an
  instrument that asks questions without it.
* **Every terminal path publishes exactly one row per respondent** - complete,
  declined, timed out or undeliverable. That is what makes a dataset countable.
* **Closings are chosen by survey outcome**, from a fixed vocabulary.
* **One execution is one respondent answering once.**

A reminder flow breaks the second and fourth: it may send three messages over a
week, collect no answer, and want one row per *send* rather than per person. A
multi-wave intervention breaks the first and fourth: consent was taken at
enrolment, not in the message, and the same person is contacted repeatedly by
design. A notification flow may publish nothing at all, because there is no
respondent-supplied data to publish.

None of those are worse flows; they are different shapes, and this format would
either fight them or quietly misdescribe them. Build them as Studio flows and
check them with ``rtt flow check``, which judges a graph on its own terms. Use
this spec when the thing being built is an instrument that asks a sampled person
questions and expects a dataset out.

Two serialisations, one schema
------------------------------
``spec.json`` is canonical. It is what git carries, what a reviewer diffs, and
the only thing the compiler reads. ``survey.xlsx`` is the same schema rendered
as a workbook - a build product, regenerated on demand, and deliberately left
under the repo-wide ``*.xlsx`` ignore rule, because a workbook in a pull request
is a binary blob nobody can review.

The workbook is the *editing* surface, so the load-bearing direction is
xlsx -> json: an edit that does not survive the trip back is silent data loss on
the instrument. :mod:`requests_to_twilio.spec_xlsx` owns that trip and is tested
for it.

Why validation lives here and not in the compiler
-------------------------------------------------
Because the defects worth catching are properties of the *spec*, not of the
graph, and they have to be caught before a round rather than after one. An
option whose label is 26 characters is rejected by Twilio at template-create
time with an error that does not say which string was too long. An option that
the split can never match is not rejected by anything at all - the respondent
taps a real row, gets the retry nudge, and is coded as having failed to answer.

:func:`check_spec` therefore does what ``check_language`` did before it, and the
part worth keeping most is that it does not read the conditions it generates -
it *runs* them, through the same :func:`~requests_to_twilio.flows.evaluate_condition`
Studio uses, and checks where each reply lands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .answers import (
    answer_pattern,
    expected_code,
    has_emoji,
    option_code,
    positions_are_ambiguous,
    word_pattern,
)
from .flows import (
    LIST_BUTTON_CHARS,
    MAX_LIST_ITEMS,
    MAX_SESSION_BUTTONS,
    TEXT_LIMITS,
    evaluate_condition,
)

#: A translatable string: language code to text. Rendered in the workbook as
#: ``label:en`` / ``label:es`` columns, matching SurveyCTO's ``label:English``.
Text = dict[str, str]

#: How a question is asked, and therefore what subgraph it becomes. The type
#: carries the rendering rather than deferring it to a separate ``appearance``
#: column: ``select_one p1`` plus ``appearance: list`` states one fact twice,
#: and two columns that have to agree are two columns that can disagree.
#:
#: ============================  ==============================  =========
#: type                          what the respondent gets        widgets
#: ============================  ==============================  =========
#: ``text``                      a plain body, any reply taken    3
#: ``integer`` / ``decimal``     a plain body, reply validated    8
#: ``select_list <list>``        ``twilio/list-picker``, ≤10      8
#: ``select_button <list>``      ``twilio/quick-reply``, ≤3       8
#: ``template``                  an approved template, waits      3
#: ============================  ==============================  =========
#:
#: ``text`` and ``integer`` look identical to a respondent and differ entirely in
#: the data: ``text`` stores whatever arrives, and ``integer`` refuses anything
#: its :attr:`SurveyRow.constraint` rejects and re-asks. Reaching for ``text`` on
#: a question that wants a number is how a column ends up holding "about 5".
QUESTION_TYPES = frozenset(
    {
        "text",
        "integer",
        "decimal",
        "numeric",
        "select_list",
        "select_button",
        "template",
    }
)

#: Open types whose reply is validated by a regex rather than by an option list.
CONSTRAINED_TYPES = frozenset({"integer", "decimal", "numeric"})

#: Written by an RA, accepted here, normalised on the way in. ``numeric`` is the
#: word people reach for; ``integer`` is XLSForm's, and says which regex applies.
TYPE_ALIASES = {"numeric": "integer"}

#: Default ``constraint`` per type - the regex a reply must match to be stored.
#: Wrapped and padded the way :mod:`requests_to_twilio.answers` wraps its
#: patterns, because Studio anchors a condition to the whole reply: an unwrapped
#: alternation can bind as ``(^a)|(b$)`` and match "xxb".
#:
#: **Unsigned on purpose.** Most numeric questions in a survey are counts, and
#: the two ways to be wrong here are not symmetric: a default that rejected
#: nothing would store "-5 times" as a real answer and quietly move a published
#: mean, while a default that is too strict nudges a respondent who meant it -
#: visible, and recoverable in the same session. For a question that genuinely
#: takes a negative value, set ``constraint`` to ``(?:\s*-?\d+\s*)``.
DEFAULT_CONSTRAINTS = {
    "integer": r"(?:\s*\d+\s*)",
    # A decimal point or a comma: which one a respondent types depends on their
    # locale, not on the instrument, and both mean the same number.
    "decimal": r"(?:\s*\d+(?:[.,]\d+)?\s*)",
}

#: Replies a numeric constraint must accept, and must refuse. Run rather than
#: eyeballed, for the same reason the option patterns are: a constraint that
#: looks right and rejects "12" strands every respondent who answered correctly,
#: and one that accepts "about 5" puts it in the data as a number.
_CONSTRAINT_PROBES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "integer": (
        ("0", "3", "12", " 7 ", "100"),
        ("", "about 5", "tres", "5 apples", "3.5", "-2"),
    ),
    "decimal": (
        ("0", "3", "3.5", "3,5", " 12.75 "),
        ("", "about 5", "tres", "5 apples", "3.5.5", "-2"),
    ),
}

#: Structural rows that are not questions.
STRUCTURE_TYPES = frozenset({"begin group", "end group", "note"})

#: What a row does in the flow's spine, when it does something special. Blank is
#: the common case: an ordinary question in the middle of the instrument.
#:
#: ``consent`` is a *question* and is typed like one - ``select_button consent``,
#: with its wording in ``label`` and its options in ``choices``, where an RA
#: reads and reviews it alongside everything else. The role exists only because
#: its subgraph differs: one re-ask rather than two, and an unreadable reply
#: recorded as ``unclear`` rather than as a refusal. Routing "what is this?" to a
#: decline would publish it as an explicit refusal, and refusal rate is a
#: headline number in a consent-based study.
ROLES = frozenset({"", "intro", "consent", "close", "unsolicited"})

#: Lookup built from the limits :mod:`requests_to_twilio.flows` documents, so
#: the spec and the flow checks cannot disagree about what Twilio accepts.
_LIMIT = {(kind, field_name): cap for kind, field_name, cap in TEXT_LIMITS}

LIST = "twilio/list-picker"
BUTTONS = "twilio/quick-reply"


class SpecError(Exception):
    """Raised when a spec cannot be read or is structurally unusable."""


@dataclass
class SurveyRow:
    """One row of the ``survey`` sheet: a question and its whole subgraph."""

    type: str
    name: str
    label: Text = field(default_factory=dict)
    role: str = ""
    relevance: str = ""
    #: Re-ask cap. ``0`` accepts whatever arrives, which is what ``text`` does.
    #: ``None`` inherits ``default_retries``.
    retries: int | None = None
    timeout: int | None = None
    #: The regex a reply must match to be stored, for the open validated types.
    #: Blank takes the default for the type from :data:`DEFAULT_CONSTRAINTS`. A
    #: select question needs none: its pattern is built from its own options.
    constraint: str = ""
    #: SurveyCTO's ``constraint message``: the nudge sent when a reply does not
    #: validate. A key into ``messages``. Blank picks ``error_option`` or
    #: ``error_option_labels`` for a select - from whether a bare typed digit
    #: would be ambiguous on this question's own options - and ``error_numeric``
    #: for a constrained open question.
    constraint_message: str = ""
    list_button: Text = field(default_factory=dict)
    #: Friendly name of a content template, per language. The two bookends Meta
    #: reviews have different names in each language, so this cannot live in the
    #: type string.
    template: Text = field(default_factory=dict)
    stop_check: bool = True
    publish: bool = True
    encrypt: bool = False

    @property
    def kind(self) -> str:
        """Return the type without its list name, normalised through the aliases.

        ``select_list p1`` becomes ``select_list``; ``numeric`` becomes
        ``integer``.
        """
        head = self.type.split()[0] if self.type else ""
        return TYPE_ALIASES.get(head, head)

    @property
    def list_name(self) -> str:
        """The choice list this question draws on, or empty for an open question."""
        parts = self.type.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    @property
    def is_question(self) -> bool:
        """Whether this row waits for a reply."""
        return self.kind in QUESTION_TYPES

    @property
    def is_select(self) -> bool:
        """Whether the reply is validated against an option list."""
        return self.kind in ("select_list", "select_button")

    @property
    def content_type(self) -> str:
        """The Twilio content type this row sends, or empty for a plain body."""
        if self.kind == "select_list":
            return LIST
        if self.kind == "select_button":
            return BUTTONS
        return ""

    @property
    def sends_template(self) -> bool:
        """Whether this row's text lives in a content template rather than here.

        True for the two bookends: the opener, which is the only
        business-initiated message and therefore the only one Meta reviews, and
        the closing sent to somebody who never replied and so never opened the
        24-hour window.
        """
        return self.kind == "template" or bool(self.template)

    @property
    def is_validated(self) -> bool:
        """Whether a reply can fail to validate, and so whether re-asking applies.

        ``text`` cannot fail: anything the respondent sends is the answer. That
        is a deliberate choice about the *instrument*, not a shortcut - ARM 1 of
        the demo accepts anything precisely so the cost of doing so is
        measurable - but it does mean a `retries` value on a ``text`` row has
        nothing to act on.
        """
        return self.is_select or self.kind in CONSTRAINED_TYPES

    def resolved_constraint(self) -> str:
        """Return the regex this row validates against, or empty if it does not."""
        if self.kind not in CONSTRAINED_TYPES:
            return ""
        return self.constraint or DEFAULT_CONSTRAINTS.get(self.kind, "")


@dataclass
class ChoiceRow:
    """One option. The 24-character cap and the tap-vs-type problem live here."""

    list_name: str
    #: The code stored in the warehouse. Explicit on every row, so a "Prefer not
    #: to say" coded ``-99`` is stated rather than inferred from its position -
    #: left to position it would be a 6 on a 5-point item and be averaged in.
    value: str
    #: What a tapped list row actually returns. A tapped *button* returns its
    #: title instead: the two interactive types disagree, which is why both are
    #: accepted. Blank generates ``<list_name>_<value>``.
    option_id: str = ""
    label: Text = field(default_factory=dict)
    description: Text = field(default_factory=dict)
    #: Extra spellings to accept, pipe-delimited - ``1|yes|y``. Pipe rather than
    #: comma on purpose: option labels contain commas and must not be split.
    typed: Text = field(default_factory=dict)

    def resolved_id(self) -> str:
        """Return the option id, generating one from the list and value if unset."""
        return self.option_id or f"{self.list_name}_{self.value}"


@dataclass
class MessageRow:
    """A string with no position in the graph: a nudge, the stop words."""

    key: str
    text: Text = field(default_factory=dict)


@dataclass
class Settings:
    """The ``settings`` sheet: one row describing the instrument as a whole."""

    form_id: str = ""
    form_title: str = ""
    languages: list[str] = field(default_factory=lambda: ["en"])
    default_language: str = "en"
    functions_service: str = "rtt-survey"
    default_timeout: int = 3600
    default_retries: int = 2
    #: Columns the sample file must supply, checked by ``rtt launch --dry-run``.
    preloads: list[str] = field(default_factory=list)
    flow_name: Text = field(default_factory=dict)
    description: Text = field(default_factory=dict)


@dataclass
class Spec:
    """A whole instrument: settings, questions, options, and boilerplate."""

    settings: Settings = field(default_factory=Settings)
    survey: list[SurveyRow] = field(default_factory=list)
    choices: list[ChoiceRow] = field(default_factory=list)
    messages: list[MessageRow] = field(default_factory=list)

    # -- lookups ----------------------------------------------------------

    def choice_list(self, list_name: str) -> list[ChoiceRow]:
        """Every option in a list, in sheet order, which is display order."""
        return [c for c in self.choices if c.list_name == list_name]

    def message(self, key: str, lang: str) -> str:
        """One boilerplate string, or empty if the spec does not carry it."""
        for row in self.messages:
            if row.key == key:
                return row.text.get(lang, "")
        return ""

    def message_list(self, key: str, lang: str) -> list[str]:
        """Read a comma-delimited message back as a list, e.g. the stop words."""
        raw = self.message(key, lang)
        return [part.strip() for part in raw.split(",") if part.strip()]

    def options(self, list_name: str, lang: str) -> list[tuple[str, str, str, str]]:
        """Return a choice list in the shape :mod:`requests_to_twilio.answers` takes.

        ``(id, label, description, code)`` - which is exactly what
        :func:`~requests_to_twilio.answers.answer_pattern`,
        :func:`~requests_to_twilio.answers.option_code` and
        :func:`~requests_to_twilio.answers.code_mapping` already consume. This
        adapter is the whole reason those helpers did not have to change when
        the spec replaced the Python language tables.
        """
        return [
            (
                choice.resolved_id(),
                choice.label.get(lang, ""),
                choice.description.get(lang, ""),
                choice.value,
            )
            for choice in self.choice_list(list_name)
        ]

    def accepted_replies(self, list_name: str, lang: str) -> list[str]:
        """Every spelling this list accepts: ids, labels, and typed alternatives."""
        accepted: list[str] = []
        for choice in self.choice_list(list_name):
            accepted.append(choice.resolved_id())
            accepted.append(choice.label.get(lang, ""))
            accepted.extend(
                part.strip()
                for part in choice.typed.get(lang, "").split("|")
                if part.strip()
            )
        return [reply for reply in accepted if reply]

    def questions(self) -> list[SurveyRow]:
        """Only the rows that wait for a reply."""
        return [row for row in self.survey if row.is_question]

    def row(self, name: str) -> SurveyRow | None:
        """One survey row by name."""
        return next((r for r in self.survey if r.name == name), None)

    def consent_row(self) -> SurveyRow | None:
        """Return the consent gate, if the instrument has one."""
        return next((r for r in self.survey if r.role == "consent"), None)

    def retries_for(self, row: SurveyRow) -> int:
        """Return a question's re-ask cap, falling back to the spec default.

        A row with nothing to validate has nothing to re-ask about, so ``text``
        is always zero however the sheet is filled in.
        """
        if not row.is_validated:
            return 0
        if row.retries is not None:
            return row.retries
        return self.settings.default_retries

    def timeout_for(self, row: SurveyRow) -> int:
        """Return a question's reply timeout in seconds, or the spec default."""
        return row.timeout if row.timeout is not None else self.settings.default_timeout

    def widget_count(self, row: SurveyRow) -> int:
        """Return how many Studio widgets this one row becomes.

        Reported in the workbook and by ``rtt survey check`` because it is the
        whole claim the spec makes: a row is not a line of configuration, it is
        a subgraph. A validated question with retries is eight widgets - ask,
        stop-check, validate, store, count the retry, decide, nudge, give up -
        and a plain ``text`` one is three. Seeing the number is what stops
        somebody adding a fifth arm and wondering why the canvas is unreadable
        again.

        Approximate by construction, and deliberately so: the spine widgets are
        shared and belong to no single row, so this counts only what the row
        itself expands into. The exact total is whatever the compiler emits.
        """
        if row.type in ("begin group", "end group"):
            return 1 if row.relevance else 0
        if not row.is_question:
            return 1
        widgets = 1  # the ask itself
        if row.stop_check:
            widgets += 1
        # The opener stores nothing. Any reply at all is the answer, because its
        # only job is to open the 24-hour window that makes every later message
        # free-form - so there is no variable to set and no validation to do.
        if row.kind == "template":
            return widgets
        if row.is_validated:
            widgets += 2  # validate, store
            if self.retries_for(row) > 0:
                widgets += 4  # count, decide, nudge, give up
        else:
            widgets += 1  # store
        return widgets

    def total_widget_count(self) -> int:
        """Return the widgets every row expands into, spine excluded."""
        return sum(self.widget_count(row) for row in self.survey)


# ---------------------------------------------------------------------------
# JSON, which is the canonical form.
# ---------------------------------------------------------------------------


def _clean(value: Any) -> Any:
    """Drop empty values so the JSON carries only what was actually set.

    A spec round-tripped through a workbook picks up an empty string for every
    unfilled cell. Writing those out would make the tracked JSON churn on edits
    that changed nothing, and the diff is the review here.
    """
    if isinstance(value, dict):
        cleaned = {
            k: _clean(v) for k, v in value.items() if v not in ("", None, {}, [])
        }
        return cleaned
    return value


def spec_to_dict(spec: Spec) -> dict[str, Any]:
    """Render a spec as the plain data that goes to JSON."""
    return {
        "settings": {
            "form_id": spec.settings.form_id,
            "form_title": spec.settings.form_title,
            "languages": list(spec.settings.languages),
            "default_language": spec.settings.default_language,
            "functions_service": spec.settings.functions_service,
            "default_timeout": spec.settings.default_timeout,
            "default_retries": spec.settings.default_retries,
            "preloads": list(spec.settings.preloads),
            "flow_name": _clean(spec.settings.flow_name),
            "description": _clean(spec.settings.description),
        },
        "survey": [
            _clean(
                {
                    "type": r.type,
                    "name": r.name,
                    "role": r.role,
                    "label": r.label,
                    "relevance": r.relevance,
                    "retries": r.retries,
                    "timeout": r.timeout,
                    "constraint": r.constraint,
                    "constraint_message": r.constraint_message,
                    "list_button": r.list_button,
                    "template": r.template,
                    # Booleans are written only when they differ from the
                    # default, so the JSON says what is unusual about a row
                    # rather than restating the obvious on every line.
                    "stop_check": None if r.stop_check else False,
                    "publish": None if r.publish else False,
                    "encrypt": True if r.encrypt else None,
                }
            )
            for r in spec.survey
        ],
        "choices": [
            _clean(
                {
                    "list_name": c.list_name,
                    "value": c.value,
                    "option_id": c.option_id,
                    "label": c.label,
                    "description": c.description,
                    "typed": c.typed,
                }
            )
            for c in spec.choices
        ],
        "messages": [_clean({"key": m.key, "text": m.text}) for m in spec.messages],
    }


def spec_from_dict(payload: dict[str, Any]) -> Spec:
    """Read a spec back from plain data, applying the documented defaults."""
    if not isinstance(payload, dict):
        raise SpecError("A survey spec must be a JSON object.")

    raw_settings = payload.get("settings") or {}
    settings = Settings(
        form_id=raw_settings.get("form_id", ""),
        form_title=raw_settings.get("form_title", ""),
        languages=list(raw_settings.get("languages") or ["en"]),
        default_language=raw_settings.get("default_language", "en"),
        functions_service=raw_settings.get("functions_service", "rtt-survey"),
        default_timeout=int(raw_settings.get("default_timeout", 3600)),
        default_retries=int(raw_settings.get("default_retries", 2)),
        preloads=list(raw_settings.get("preloads") or []),
        flow_name=dict(raw_settings.get("flow_name") or {}),
        description=dict(raw_settings.get("description") or {}),
    )

    survey = [
        SurveyRow(
            type=r.get("type", ""),
            name=r.get("name", ""),
            label=dict(r.get("label") or {}),
            role=r.get("role", ""),
            constraint=r.get("constraint", ""),
            constraint_message=r.get("constraint_message", ""),
            relevance=r.get("relevance", ""),
            retries=r.get("retries"),
            timeout=r.get("timeout"),
            list_button=dict(r.get("list_button") or {}),
            template=dict(r.get("template") or {}),
            stop_check=r.get("stop_check", True),
            publish=r.get("publish", True),
            encrypt=r.get("encrypt", False),
        )
        for r in payload.get("survey") or []
    ]

    choices = [
        ChoiceRow(
            list_name=c.get("list_name", ""),
            value=str(c.get("value", "")),
            option_id=c.get("option_id", ""),
            label=dict(c.get("label") or {}),
            description=dict(c.get("description") or {}),
            typed=dict(c.get("typed") or {}),
        )
        for c in payload.get("choices") or []
    ]

    messages = [
        MessageRow(key=m.get("key", ""), text=dict(m.get("text") or {}))
        for m in payload.get("messages") or []
    ]

    return Spec(settings=settings, survey=survey, choices=choices, messages=messages)


def load_spec(path: Path) -> Spec:
    """Read a spec from JSON.

    Raises:
        SpecError: If the file cannot be read or parsed.

    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpecError(f"Could not read {path}: {exc}") from exc
    return spec_from_dict(payload)


def save_spec(spec: Spec, path: Path) -> Path:
    """Write a spec as JSON - the tracked, reviewable form."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec_to_dict(spec), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------


def check_spec(spec: Spec) -> list[str]:
    """Return every way this spec would produce a broken instrument.

    Args:
        spec: The instrument to check.

    Returns:
        Human-readable problems, in the order they were found. Empty means the
        spec is usable.

    These are Twilio's and Meta's limits rather than house style, and each one
    fails in a way that is hard to spot by reading. An over-long item title is
    rejected at template-create time with a generic error that does not name the
    string. A comma inside a label is not rejected at all - it used to produce a
    condition that could never match, so the respondent tapped a real option and
    landed on the retry nudge.

    The reachability checks are the reason this function exists rather than a
    style linter: they *run* every condition the compiler will generate, through
    the same evaluator Studio uses, and confirm where each possible reply lands.
    A condition that looks right and matches nothing is the same class of defect
    as a break-off that publishes no row - invisible in the editor, obvious only
    in the data, months later.

    """
    problems: list[str] = []
    problems.extend(_check_structure(spec))
    problems.extend(_check_text_limits(spec))
    problems.extend(_check_constraints(spec))
    problems.extend(_check_options_are_matchable(spec))
    problems.extend(_check_consent(spec))
    return problems


def _check_constraints(spec: Spec) -> list[str]:
    """Run each numeric constraint against replies it must take and must refuse.

    A custom ``constraint`` is a regex an RA wrote, which means it is a small
    program nobody tested. The failure is silent in both directions: too strict
    and every respondent who answered correctly gets nudged and eventually
    recorded as unable to answer; too loose and "about 5" lands in a column the
    analyst will average.

    The probes are deliberately the replies people actually send. A pattern that
    passes them is not proven correct, but a pattern that fails them is
    definitely wrong, and that is the useful direction.
    """
    problems: list[str] = []
    for row in spec.survey:
        if row.kind not in CONSTRAINED_TYPES:
            continue
        pattern = row.resolved_constraint()
        if not pattern:
            problems.append(
                f"{row.name}: type is {row.type!r} but there is no constraint to "
                f"validate against and no default for that type"
            )
            continue

        accept, refuse = _CONSTRAINT_PROBES.get(row.kind, ((), ()))
        # A custom constraint is judged only on what it must accept. Somebody who
        # writes their own pattern may well mean to allow "-2" or "1,000", and
        # calling that a defect would make the check the thing to switch off.
        custom = bool(row.constraint)
        for reply in accept:
            if not evaluate_condition("regex", pattern, reply):
                problems.append(
                    f"{row.name}: constraint rejects {reply!r}, which is a valid "
                    f"{row.kind} answer - the respondent would be nudged for "
                    f"answering correctly"
                )
        if custom:
            continue
        for reply in refuse:
            if evaluate_condition("regex", pattern, reply):
                problems.append(
                    f"{row.name}: constraint accepts {reply!r}, so it would be "
                    f"stored as a {row.kind} answer"
                )
    return problems


def _check_structure(spec: Spec) -> list[str]:
    """Names, types, roles, groups, and the references between sheets."""
    problems: list[str] = []
    known = QUESTION_TYPES | STRUCTURE_TYPES

    if not spec.settings.languages:
        problems.append("settings: no languages listed, so nothing can be built")
    if spec.settings.default_language not in spec.settings.languages:
        problems.append(
            f"settings: default_language {spec.settings.default_language!r} is not "
            f"in languages {spec.settings.languages}"
        )

    seen: dict[tuple[str, str], int] = {}
    depth = 0
    group: str = ""
    for index, row in enumerate(spec.survey, start=1):
        where = f"survey row {index} ({row.name or 'unnamed'})"

        # `kind` is the first word of the type, which is right for
        # `select_list p1` and wrong for `begin group` - so the two-word
        # structural types are matched whole, before falling back to it.
        if row.type not in STRUCTURE_TYPES and row.kind not in known:
            problems.append(f"{where}: unknown type {row.type!r}")
        if row.role not in ROLES:
            problems.append(
                f"{where}: unknown role {row.role!r}; expected one of "
                + ", ".join(sorted(r for r in ROLES if r))
            )

        if row.type == "begin group":
            depth += 1
            group = row.name
            continue
        if row.type == "end group":
            depth -= 1
            group = ""
            if depth < 0:
                problems.append(f"{where}: end group without a matching begin group")
            continue

        if not row.name:
            problems.append(f"{where}: every row needs a name")
        # Scoped to the group, because ARM1 and ARM2 deliberately both hold a
        # question called P1 - that is what makes them the same question asked
        # two ways, and what lets the compiler emit ARM1_P1 and ARM2_P1.
        key = (group, row.name)
        if key in seen:
            problems.append(
                f"{where}: name {row.name!r} is already used by row {seen[key]}"
                + (f" in group {group!r}" if group else "")
            )
        seen[key] = index

        if row.list_name and not spec.choice_list(row.list_name):
            problems.append(
                f"{where}: type is {row.type!r} but the choices sheet has no "
                f"list called {row.list_name!r}"
            )
        if row.is_select and not row.list_name:
            problems.append(
                f"{where}: {row.kind} needs a list name, as in '{row.kind} my_list'"
            )
        if row.constraint and row.kind not in CONSTRAINED_TYPES:
            problems.append(
                f"{where}: sets a constraint, but a {row.kind!r} question does "
                "not validate a reply against one. A select question's pattern "
                "is built out of its own options; `text` accepts anything by "
                "design - use `integer` or `decimal` if the reply has to be "
                "checked"
            )
        if row.constraint_message and not spec.message(
            row.constraint_message, spec.settings.default_language
        ):
            problems.append(
                f"{where}: constraint_message {row.constraint_message!r} is not "
                "a key in the messages sheet"
            )
        if row.retries and not row.is_validated:
            problems.append(
                f"{where}: retries is {row.retries} on a {row.kind!r} question, "
                "which cannot fail - anything the respondent sends is the "
                "answer, so there is nothing to re-ask. Use `integer`, "
                "`decimal` or a select if the reply has to be checked"
            )

        for lang in spec.settings.languages:
            # A template-backed row legitimately has no label here. The two
            # bookends Meta reviews carry their copy in templates/<name>.json,
            # and that file is deliberately its only home: the widget references
            # the template by SID and holds no text, so there is nothing for the
            # two to disagree about. Duplicating the body into the spec would
            # reintroduce exactly that.
            if row.sends_template:
                if not row.template.get(lang):
                    problems.append(
                        f"{where}: sends a template but names none for language "
                        f"{lang!r}"
                    )
                continue
            if row.is_question and not row.label.get(lang):
                problems.append(f"{where}: no label for language {lang!r}")

    if depth > 0:
        problems.append("survey: a begin group was never closed by an end group")

    consent_list = consent.list_name if (consent := spec.consent_row()) else ""
    for choice in spec.choices:
        if not choice.list_name:
            problems.append("choices: a row has no list_name")
        # `typed` is read by the consent routing, which builds its pattern from
        # the labels plus these alternatives. An ordinary question's pattern
        # comes from `answer_pattern`, which accepts the id, the label and the
        # typed position - and knows nothing about this column. Silently
        # ignoring it would mean the sheet promises to accept a spelling that
        # the flow refuses, and the respondent who used it gets the retry nudge.
        if choice.typed and choice.list_name != consent_list:
            problems.append(
                f"choices: {choice.list_name}/{choice.value} sets `typed`, which "
                "is only wired up for the consent list so far. Anywhere else it "
                "would be accepted here and refused by the flow - leave it "
                "blank, or add the alternative as its own option"
            )
        if choice.value == "":
            problems.append(
                f"choices: {choice.list_name!r} has an option with no value, so "
                "there is no code to store for it"
            )
        for lang in spec.settings.languages:
            if not choice.label.get(lang):
                problems.append(
                    f"choices: {choice.list_name}/{choice.value} has no label for "
                    f"language {lang!r}"
                )

    return problems


def _check_text_limits(spec: Spec) -> list[str]:
    """Check the caps Twilio and WhatsApp enforce, plus the emoji rule."""
    problems: list[str] = []

    for lang in spec.settings.languages:
        for row in spec.survey:
            if not row.is_question:
                continue
            body = row.label.get(lang, "")
            cap = _LIMIT[(LIST, "body")]
            if row.content_type and len(body) > cap:
                problems.append(
                    f"{lang}: {row.name} body is {len(body)} chars, cap is {cap}"
                )

            if row.content_type == LIST:
                button = row.list_button.get(lang) or spec.message("list_button", lang)
                if len(button) > LIST_BUTTON_CHARS:
                    problems.append(
                        f"{lang}: {row.name} list button {button!r} exceeds the "
                        f"{LIST_BUTTON_CHARS} characters WhatsApp shows"
                    )

            options = spec.choice_list(row.list_name) if row.list_name else []
            if row.content_type == LIST and not 1 <= len(options) <= MAX_LIST_ITEMS:
                problems.append(
                    f"{lang}: {row.name} has {len(options)} options, a list "
                    f"picker allows 1 to {MAX_LIST_ITEMS}"
                )
            if (
                row.content_type == BUTTONS
                and not 1 <= len(options) <= MAX_SESSION_BUTTONS
            ):
                problems.append(
                    f"{lang}: {row.name} has {len(options)} options. Sent in "
                    f"session without Meta approval, WhatsApp permits "
                    f"{MAX_SESSION_BUTTONS} buttons and the send fails past that"
                )

        for choice in spec.choices:
            label = choice.label.get(lang, "")
            description = choice.description.get(lang, "")
            named = f"{choice.list_name}/{choice.value}"

            # A label is compared literally against the reply body, so which cap
            # applies depends on how the list is rendered. Taking the stricter of
            # the two costs nothing and a list is the default rendering here.
            if len(label) > _LIMIT[(LIST, "item")]:
                problems.append(
                    f"{lang}: {named} label is {len(label)} chars, cap is "
                    f"{_LIMIT[(LIST, 'item')]}: {label!r}"
                )
            if len(description) > _LIMIT[(LIST, "description")]:
                problems.append(
                    f"{lang}: {named} description is {len(description)} chars, "
                    f"cap is {_LIMIT[(LIST, 'description')]}"
                )
            if len(choice.resolved_id()) > _LIMIT[(LIST, "id")]:
                problems.append(f"{lang}: {named} option_id exceeds 200 chars")
            if has_emoji(label):
                problems.append(
                    f"{lang}: {named} label contains an emoji. Labels are matched "
                    f"literally against the reply, and a variation selector makes "
                    f"two identical-looking strings different - put warmth in the "
                    f"body, which nothing matches on: {label!r}"
                )

        for list_name in {c.list_name for c in spec.choices}:
            labels = [
                c.label.get(lang, "").casefold() for c in spec.choice_list(list_name)
            ]
            duplicated = {label for label in labels if labels.count(label) > 1}
            for label in sorted(duplicated):
                problems.append(
                    f"{lang}: {list_name} repeats the label {label!r}, so the "
                    "code stored for it would be ambiguous"
                )

    return problems


def _check_options_are_matchable(spec: Spec) -> list[str]:
    """Run every option through its own split condition and see where it lands.

    Reading a condition and believing it is how an unreachable option survives
    review. This executes it instead, using the same semantics Studio uses, and
    checks four things that have to hold: every label routes to the store
    widget, every position typed as a digit routes there too *where a digit is
    unambiguous*, a digit that is ambiguous is refused rather than guessed at,
    and something nobody would ever tap does not match.

    The last two matter as much as the first two. A pattern loose enough to
    match anything accepts junk as a real answer, which is worse than rejecting
    a real one - the respondent is never asked again and the row looks complete.
    And a pattern that accepts an ambiguous digit stores the wrong answer, which
    is worse still, because it is indistinguishable from a right one.
    """
    problems: list[str] = []

    for lang in spec.settings.languages:
        for row in spec.survey:
            if not row.list_name or not row.is_question:
                continue
            options = spec.options(row.list_name, lang)
            if not options:
                continue
            pattern = answer_pattern(options)
            ambiguous = positions_are_ambiguous(options)
            named = f"{lang}: {row.name}"

            for index, option in enumerate(options, start=1):
                option_id, item = option[0], option[1]
                # The id first: that is what a tapped list row actually sends.
                # Discovered the hard way - a live test answered `p1_0` where
                # the split expected "0 times", so every tap fell to the retry.
                must_accept = [option_id, item, f" {item} ", item.upper()]
                if not ambiguous:
                    must_accept += [str(index), f"{index}."]
                for reply in must_accept:
                    if not evaluate_condition("regex", pattern, reply):
                        problems.append(
                            f"{named} would not accept {reply!r}, so the option "
                            f"{item!r} is unreachable"
                        )

            # The collision itself. On a scale whose labels are numbers,
            # position N and label N mean different options, and accepting the
            # bare digit picks the wrong one silently.
            if ambiguous:
                for index in range(1, len(options) + 1):
                    if evaluate_condition("regex", pattern, str(index)):
                        problems.append(
                            f"{named} has numeric option labels and still accepts "
                            f"the bare digit {index!r}. A respondent typing it "
                            f"means the label, not the position, and would be "
                            f"coded as the wrong option"
                        )

            for junk in ("banana", "", "0", str(len(options) + 1), "yes please"):
                if junk in spec.accepted_replies(row.list_name, lang):
                    continue
                if evaluate_condition("regex", pattern, junk):
                    problems.append(
                        f"{named} accepts {junk!r} as a valid answer, so junk "
                        "would be stored as a real response"
                    )

            # The split and the code mapping must agree. If the split is the
            # more tolerant of the two, a respondent is recorded as having
            # answered while their answer codes as `other` - which reads in the
            # data as a broken option rather than as the tolerance working.
            for index, option in enumerate(options, start=1):
                item = option[1]
                wanted = option_code(option, index)
                for reply in (
                    item,
                    str(index),
                    f"{index}.",
                    f"({index})",
                    item.upper(),
                ):
                    accepted = evaluate_condition("regex", pattern, reply)
                    code = expected_code(options, reply)
                    if accepted and code == "other":
                        problems.append(
                            f"{named} accepts {reply!r} but codes it as 'other'; "
                            "the split is more tolerant than the mapping"
                        )
                    if accepted and code not in ("other", wanted):
                        problems.append(
                            f"{named} codes {reply!r} as {code}, expected {wanted}"
                        )

    return problems


def _check_consent(spec: Spec) -> list[str]:
    """Check the consent gate routes yes to yes, no to no, and nothing to both."""
    row = spec.consent_row()
    if row is None:
        if spec.questions():
            return [
                "survey: no row has role 'consent'. An instrument that asks "
                "questions without recording consent cannot be run at IPA"
            ]
        return []

    problems: list[str] = []
    options = spec.choice_list(row.list_name)
    if len(options) < 2:
        return problems + [
            f"consent: {row.list_name!r} needs at least a yes and a no option"
        ]

    for lang in spec.settings.languages:
        patterns = []
        for choice in options:
            words = [choice.label.get(lang, "")] + [
                part.strip()
                for part in choice.typed.get(lang, "").split("|")
                if part.strip()
            ]
            patterns.append((choice.value, word_pattern(words), words))

        for value, pattern, replies in patterns:
            for reply in replies:
                if not evaluate_condition("regex", pattern, reply):
                    problems.append(
                        f"{lang}: consent branch {value!r} would not accept {reply!r}"
                    )

        # Consent is the one place an ambiguous match is unacceptable: a reply
        # satisfying two branches would enrol someone by transition order.
        for first_index, (first_value, first_pattern, _) in enumerate(patterns):
            for second_value, second_pattern, replies in patterns[first_index + 1 :]:
                for reply in replies:
                    if evaluate_condition(
                        "regex", first_pattern, reply
                    ) and evaluate_condition("regex", second_pattern, reply):
                        problems.append(
                            f"{lang}: consent reply {reply!r} matches both "
                            f"{first_value!r} and {second_value!r}, so "
                            "participation would be decided by transition order"
                        )

    return problems


def review_notes(spec: Spec) -> list[str]:
    """Things a human must read, which no check can judge.

    Separate from :func:`check_spec` because these are not defects and must not
    fail a build - but they must not be silent either. Consent wording is the
    clear case: it is the one string in the instrument with an IRB behind it,
    every check here can pass on wording that is misleading or coercive, and by
    the time it is wrong somebody has already agreed to something.
    """
    notes: list[str] = []
    consent = spec.consent_row()
    if consent is not None:
        for lang in spec.settings.languages:
            body = consent.label.get(lang, "")
            if body:
                notes.append(
                    f"{lang}: read the consent wording by hand. No check can tell "
                    f"whether it is accurate, voluntary and comprehensible - and "
                    f"it is approved text, so an edit here needs the IRB that "
                    f"approved it:\n      " + body.replace("\n", "\n      ")
                )
    return notes


# ---------------------------------------------------------------------------
# The starter instrument, for `rtt survey template`.
# ---------------------------------------------------------------------------

#: Said by `rtt survey template`, written into the workbook's help sheet, and
#: repeated in docs/writing-a-survey.md. It is the one thing about this format
#: that cannot be inferred from the columns, and getting it wrong wastes days:
#: somebody describes a reminder campaign as a survey, fights the consent check,
#: and ends up with a flow whose shape does not match what it is for.
SCOPE_NOTE = (
    "This format describes a SURVEY: an instrument that asks a sampled person "
    "questions and expects a dataset out. It assumes consent is asked before "
    "any question, that every path publishes exactly one row per respondent, "
    "and that one execution is one person answering once.\n\n"
    "Reminders, multi-wave interventions and notifications break those "
    "assumptions - they may send repeatedly to the same person, collect no "
    "answer, take consent at enrolment rather than in the message, or publish "
    "nothing. Those are not worse flows, they are a different shape. Build them "
    "as Studio flows and check them with `rtt flow check`, which judges a graph "
    "on its own terms."
)


def starter_spec(language: str = "en") -> Spec:
    """Build a small, valid instrument showing one row of every type.

    Args:
        language: The language code to write the example text under.

    Returns:
        A spec that passes :func:`check_spec`.

    Not an empty workbook, deliberately. A blank sheet with a header row teaches
    nothing about a format whose whole point is that a row expands into a
    subgraph - the first question anybody has is what a filled-in row looks
    like, and a template that cannot answer it sends them off to find an
    existing survey and copy it. Copying an existing survey is how seven flows
    came to share one identical break-off defect.

    It is also *valid*, so ``rtt survey check`` on an untouched template is
    clean. That matters more than it sounds: it means the first finding somebody
    ever sees is about something they did, which is when a check is worth
    reading.

    """
    text = language

    return Spec(
        settings=Settings(
            form_id="my_survey",
            form_title="Replace this with what the round is called",
            languages=[text],
            default_language=text,
            functions_service="rtt-survey",
            default_timeout=3600,
            default_retries=2,
            # What the sample file must carry. `rtt launch --dry-run` checks the
            # spreadsheet against these before anything is sent.
            preloads=["caseid", "name", "sent_at"],
            flow_name={text: "my_survey"},
            description={text: "One line saying what this round is for"},
        ),
        survey=[
            SurveyRow(
                type="template",
                name="intro",
                role="intro",
                # The only message Meta reviews, because it is the only one sent
                # before the respondent has said anything. Its copy lives in
                # templates/<name>.json, not here.
                template={text: "my_survey_intro"},
                retries=0,
                stop_check=False,
                publish=False,
            ),
            SurveyRow(
                type="select_button consent",
                name="consent",
                role="consent",
                label={
                    text: (
                        "Before we start - would you like to take part?\n\n"
                        "REPLACE THIS. It takes about N minutes. Taking part is "
                        "voluntary, you can stop at any time, and your answers "
                        "are confidential."
                    )
                },
                retries=1,
                stop_check=False,
            ),
            SurveyRow(
                type="select_list frequency",
                name="Q1",
                label={
                    text: (
                        "Question 1 of 3\n\n"
                        "A closed question. A tap cannot be malformed, so there "
                        "is nothing to clean afterwards - prefer this shape."
                    )
                },
                retries=2,
            ),
            SurveyRow(
                type="integer",
                name="Q2",
                label={
                    text: (
                        "Question 2 of 3\n\n"
                        "How many? The reply is checked before it is stored, so "
                        "'about five' is re-asked rather than saved.\n\n"
                        "_Reply with an exact number (e.g. 0, 3, 12)._"
                    )
                },
                retries=2,
                constraint_message="error_numeric",
            ),
            SurveyRow(
                type="text",
                name="Q3",
                label={
                    text: (
                        "Question 3 of 3\n\n"
                        "An open question. Whatever arrives is stored, including "
                        "an answer nobody can code - so use it only where the "
                        "answer genuinely cannot be enumerated."
                    )
                },
                retries=0,
            ),
            SurveyRow(
                type="note",
                name="close_complete",
                role="close",
                relevance="${outcome}='complete'",
                label={text: "Thank you for completing the survey."},
                publish=False,
            ),
            SurveyRow(
                type="note",
                name="close_incomplete",
                role="close",
                relevance="${outcome}='incomplete'",
                label={text: "Thank you for the answers you gave - they are recorded."},
                publish=False,
            ),
            SurveyRow(
                type="note",
                name="close_declined",
                role="close",
                relevance="${outcome}='declined'",
                label={
                    text: "Thank you for your reply. We respect your decision "
                    "not to take part."
                },
                publish=False,
            ),
            SurveyRow(
                type="note",
                name="close_optout",
                role="close",
                relevance="${outcome}='optout'",
                label={
                    text: "Understood - nothing further will be sent about this "
                    "survey. The answers you already gave are kept."
                },
                publish=False,
            ),
            SurveyRow(
                type="note",
                name="close_never_started",
                role="close",
                # Never replied, so the 24-hour window never opened and only an
                # approved template can reach them. The second bookend.
                relevance="${outcome}='unreachable'",
                template={text: "my_survey_close"},
                publish=False,
            ),
            SurveyRow(
                type="note",
                name="unsolicited_reply",
                role="unsolicited",
                label={
                    text: "Thanks for your message. This number only runs a "
                    "short survey, so nothing further is needed from you here."
                },
                publish=False,
            ),
        ],
        choices=[
            ChoiceRow(
                list_name="consent",
                value="yes",
                option_id="consent_yes",
                label={text: "Yes, I will take part"},
                typed={text: "1|yes|y"},
            ),
            ChoiceRow(
                list_name="consent",
                value="no",
                option_id="consent_no",
                label={text: "No, thanks"},
                typed={text: "2|no|n"},
            ),
            # A Likert whose labels carry no leading number, so a respondent who
            # types the position is understood. Compare a `0 / 1 / 2-3` scale,
            # where a typed digit has two readings and is refused instead.
            ChoiceRow(
                list_name="frequency",
                value="1",
                label={text: "Never"},
                description={text: "Optional second line, up to 72 characters"},
            ),
            ChoiceRow(list_name="frequency", value="2", label={text: "Rarely"}),
            ChoiceRow(list_name="frequency", value="3", label={text: "Sometimes"}),
            ChoiceRow(list_name="frequency", value="4", label={text: "Often"}),
            # Offered on the scale but not a point on it. Left to its position
            # this would code as 5 and be averaged in with "Often".
            ChoiceRow(
                list_name="frequency",
                value="-99",
                label={text: "Prefer not to say"},
                description={text: "Coded -99 so it is not averaged in as a 5"},
            ),
        ],
        messages=[
            MessageRow(
                key="error_option",
                text={
                    text: (
                        "No problem - I could not read that one.\n\n"
                        "Tap *{button}* on the message above and pick from the "
                        "list. You can also reply with the number of your answer."
                    )
                },
            ),
            MessageRow(
                key="error_option_labels",
                text={
                    text: (
                        "No problem - I could not read that one.\n\n"
                        "Tap *{button}* on the message above and pick from the "
                        "list, or type the option exactly as it appears."
                    )
                },
            ),
            MessageRow(
                key="error_numeric",
                text={
                    text: (
                        "Please reply with a number only.\n\n"
                        "_Reply with an exact number (e.g. 0, 3, 12)._"
                    )
                },
            ),
            MessageRow(key="list_button", text={text: "Choose an answer"}),
            MessageRow(
                key="stop_words",
                # The English words belong here whatever the survey's language:
                # people type STOP regardless of what they are answering in.
                text={text: "stop, quit, unsubscribe, cancel, end"},
            ),
            MessageRow(
                key="unsolicited",
                text={text: "Thanks for your message. Nothing further is needed."},
            ),
        ],
    )
