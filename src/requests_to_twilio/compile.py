"""Compile a survey spec into a Twilio Studio flow definition.

This is the last arrow in ``spec.json -> Studio flow``. One spec row becomes the
whole subgraph a question needs - ask, check for a stop word, validate, store,
re-ask, give up, remind - and the rows are wrapped in the survey spine every IPA
flow shares: the opener, consent, the outcome markers, encryption, one publish
step every path reaches, and a closing per outcome.

The spine is the demo builder's (``scripts/build_data_use_demo.py``), which is
where it was proven against real rounds; this module generalises it from two
hard-coded arms to whatever the spec describes. :func:`compile_spec` is pure -
content SIDs and Functions coordinates come in as arguments - so a flow can be
built and checked offline, and only ``rtt survey build`` talks to Twilio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .answers import answer_pattern, code_mapping, positions_are_ambiguous, word_pattern
from .outcomes import ENCRYPTION_FAILED_STATUS, final_status_liquid
from .spec import (
    COLUMN_REFERENCE,
    RANGE_MESSAGE,
    Spec,
    SpecError,
    SurveyRow,
    check_spec,
    describe_range,
    parse_condition,
    parse_relevance,
)
from .widgets import (
    BuildError,
    ask,
    ask_content,
    run_function,
    send,
    send_content,
    set_vars,
    split,
)

#: Rows the spine places itself, rather than the walk over the instrument.
_SPINE_ROLES = ("intro", "consent", "close", "unsolicited")

#: The roll-up every closing is chosen from, and the outcome nothing can reach:
#: a message that was never delivered will not deliver a goodbye either.
_SILENT_OUTCOMES = ("undeliverable",)


#: A condition's operator, as Studio's split names it and as a person reads it.
_COMPARISONS = {
    "=": ("equal_to", "is"),
    "!=": ("not_equal_to", "is not"),
    ">": ("greater_than", "is more than"),
    "<": ("less_than", "is less than"),
}


def as_number(liquid: str) -> str:
    """Make a ``{{...}}`` value a number for Studio's > and <, either decimal mark.

    Studio compares numbers, not text, and a reply arrives as text: ``| plus: 0``
    is the conversion Twilio documents. A comma becomes a point first, because
    "3,5" is how half the world writes 3.5.
    """
    inner = liquid.strip()[2:-2].strip()
    return f"{{{{{inner} | strip | replace: ',', '.' | plus: 0}}}}"


def personalise(text: str) -> str:
    """Turn each ``${column}`` into the sample-file value Studio fills in."""
    return COLUMN_REFERENCE.sub(lambda m: f"{{{{flow.data.{m.group(1)}}}}}", text)


def numbered_columns(text: str) -> tuple[str, list[str]]:
    """Turn each ``${column}`` into a content placeholder ``{{1}}``, ``{{2}}``...

    Twilio content is sent by its SID, so Studio cannot fill a value into its
    text; it fills numbered placeholders from the widget's content variables
    instead. Returns the text and the columns, in placeholder order.
    """
    columns: list[str] = []

    def number(match: re.Match[str]) -> str:
        if match.group(1) not in columns:
            columns.append(match.group(1))
        return f"{{{{{columns.index(match.group(1)) + 1}}}}}"

    return COLUMN_REFERENCE.sub(number, text), columns


def column_variables(columns: list[str]) -> list[dict[str, str]] | None:
    """Return the content variables that fill :func:`numbered_columns`' placeholders."""
    if not columns:
        return None
    return [
        {"key": str(n), "value": f"{{{{flow.data.{column}}}}}"}
        for n, column in enumerate(columns, start=1)
    ]


def content_variables(row: SurveyRow) -> list[dict[str, str]] | None:
    """Fill an approved message's placeholders from the sample file's columns.

    No default for a blank cell: an empty variable fails the whole send (error
    21656), and ``rtt launch`` refuses a sample with a blank cell in a column
    any of these reads, so a blank never reaches Twilio.
    """
    if not row.variables:
        return None
    return [
        {"key": placeholder, "value": f"{{{{flow.data.{column}}}}}"}
        for placeholder, column in sorted(
            row.variables.items(), key=lambda item: (len(item[0]), item[0])
        )
    ]


def template_name(spec: Spec, widget: str, lang: str) -> str:
    """Friendly name of the in-session content template a widget sends."""
    return f"{spec.settings.form_id}_{widget.lower()}_{lang}"


@dataclass
class _Group:
    row: SurveyRow
    children: list[Any] = field(default_factory=list)


@dataclass
class _Answer:
    """Where an earlier answer lives, for a later condition to read."""

    widget: str
    kind: str  # "select", "number" or "text"
    source: str  # Liquid expression holding the raw reply, without braces

    def value(self) -> str:
        if self.kind == "select":
            return f"{{{{flow.variables.{self.widget}_code}}}}"
        if self.kind == "number":
            return f"{{{{flow.variables.{self.widget}_value}}}}"
        return f"{{{{{self.source}}}}}"


def _tree(spec: Spec) -> list[Any]:
    """Nest the instrument's rows by group, leaving out the spine's own rows."""
    root: list[Any] = []
    stack = [root]
    for row in spec.survey:
        if row.role in _SPINE_ROLES:
            continue
        if row.type == "begin group":
            group = _Group(row)
            stack[-1].append(group)
            stack.append(group.children)
        elif row.type == "end group":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(row)
    return root


def _widget(row: SurveyRow, group: str) -> str:
    return f"{group}_{row.name}" if group else row.name


def selects(spec: Spec) -> list[tuple[SurveyRow, str]]:
    """Every select question with its widget name, consent included."""
    found: list[tuple[SurveyRow, str]] = []
    group = ""
    for row in spec.survey:
        if row.type == "begin group":
            group = row.name
        elif row.type == "end group":
            group = ""
        elif row.is_select:
            found.append(
                (row, "consent" if row.role == "consent" else _widget(row, group))
            )
    return found


def template_definitions(spec: Spec, lang: str) -> dict[str, dict[str, Any]]:
    """Every in-session content template the flow sends, by friendly name.

    Buttons become ``twilio/quick-reply`` and lists ``twilio/list-picker``, each
    with a numbered ``twilio/text`` fallback for a channel that renders neither.
    None of them is ever submitted to Meta: they are sent inside the 24-hour
    window the opener's reply opens.
    """
    definitions: dict[str, dict[str, Any]] = {}
    for row, widget in selects(spec):
        name = template_name(spec, widget, lang)
        body, columns = numbered_columns(row.label.get(lang, ""))
        choices = spec.choice_list(row.list_name)
        numbered = "\n".join(
            f"{index} - {choice.label.get(lang, '')}"
            for index, choice in enumerate(choices, start=1)
        )
        types: dict[str, Any] = {"twilio/text": {"body": f"{body}\n\n{numbered}"}}
        if row.kind == "select_button":
            types["twilio/quick-reply"] = {
                "body": body,
                "actions": [
                    {"title": c.label.get(lang, ""), "id": c.resolved_id()}
                    for c in choices
                ],
            }
        else:
            types["twilio/list-picker"] = {
                "body": body,
                "button": row.list_button.get(lang)
                or spec.message("list_button", lang),
                "items": [
                    {
                        "id": c.resolved_id(),
                        "item": c.label.get(lang, ""),
                        "description": c.description.get(lang, ""),
                    }
                    for c in choices
                ],
            }
        definitions[name] = {
            "_comment": [
                f"GENERATED by `rtt survey content` from {spec.settings.form_id}.",
                "Edit the survey spec and regenerate; do not hand-edit. Sent inside",
                "the 24-hour window, so it is never submitted to Meta.",
            ],
            "friendly_name": name,
            "language": lang,
            "types": types,
        }
        if columns:
            # Samples, as Twilio asks for them; the flow fills the real values.
            definitions[name]["variables"] = {
                str(n): column for n, column in enumerate(columns, start=1)
            }
    return definitions


def required_templates(spec: Spec, lang: str) -> list[str]:
    """Every content template the flow references: generated and approved."""
    names = list(template_definitions(spec, lang))
    for row in spec.survey:
        if row.template.get(lang):
            names.append(row.template[lang])
    return names


def placeholder_sids(spec: Spec, lang: str) -> dict[str, str]:
    """HX SIDs shaped like real ones, for an offline build that is never deployed."""
    return {
        name: f"HX{index:032d}"
        for index, name in enumerate(required_templates(spec, lang), start=1)
    }


class _Compiler:
    """One language's flow, built in a single forward walk over the spec."""

    def __init__(self, spec, lang, content_sids, functions):
        self.spec = spec
        self.lang = lang
        self.sids = content_sids
        self.functions = functions
        self.states: list[dict[str, Any]] = []
        self.answers: dict[tuple[str, str], _Answer] = {}
        self.columns: list[tuple[str, str]] = []
        self.encrypted: list[tuple[str, str]] = []
        self.condition_preloads: list[str] = []
        self.y = 0
        self.lanes: dict[str, int] = {}
        stop_words = spec.stop_word_list(lang)
        self.stop_pattern = word_pattern(stop_words)

    # -- small helpers ------------------------------------------------------

    def sid(self, name: str) -> str:
        sid = self.sids.get(name)
        if not sid:
            raise BuildError(f"no content SID for {name!r}; create the template first")
        return sid

    def text(self, row: SurveyRow) -> str:
        return personalise(row.label.get(self.lang, ""))

    def message(self, key: str) -> str:
        return personalise(self.spec.message(key, self.lang))

    def place(self, group: str) -> tuple[int, int]:
        x = self.lanes.setdefault(group, 1100 * len(self.lanes)) if group else 0
        self.y += 320
        return x, self.y

    def stop_split(self, name, reply, on_continue, *, x, y):
        return split(
            name,
            f"{{{{{reply}}}}}",
            [(self.stop_pattern, "mark_optout", "asked to stop")],
            on_continue,
            x=x,
            y=y,
            condition="regex",
        )

    def condition_splits(self, name, condition, on_true, on_false, group, *, x, y):
        """Return one split per comparison, chained: and needs each, or any one.

        One split per comparison rather than several conditions on one branch:
        a split tests a single value, and the comparisons read different ones.
        """
        parsed = parse_relevance(condition)
        if parsed is None:
            raise BuildError(f"cannot compile the condition {condition!r}")
        joiner, clauses = parsed
        names = [name] + [f"{name}_{n}" for n in range(2, len(clauses) + 1)]
        splits = []
        for index, (variable, operator, value) in enumerate(clauses):
            following = names[index + 1] if index + 1 < len(names) else None
            if joiner == "or":
                on_match, on_miss = on_true, following or on_false
            else:
                on_match, on_miss = following or on_true, on_false
            tested = self.resolve(variable, group)
            kind, words = _COMPARISONS[operator]
            if operator in "<>":
                tested, value = as_number(tested), value.replace(",", ".")
            splits.append(
                split(
                    names[index],
                    tested,
                    [(value, on_match, f"{variable} {words} {value}", kind)],
                    on_miss,
                    x=x + 280 * index,
                    y=y,
                )
            )
        return splits

    def resolve(self, variable: str, group: str) -> str:
        """Return the Liquid that reads a condition's variable."""
        answer = self.answers.get((group, variable)) or self.answers.get(("", variable))
        if answer is not None:
            return answer.value()
        if variable in self.spec.settings.preloads:
            if variable not in self.condition_preloads:
                self.condition_preloads.append(variable)
            return f"{{{{flow.data.{variable}}}}}"
        raise BuildError(
            f"a condition reads {variable!r}, which is neither a preload nor an "
            "earlier question"
        )

    # -- the walk ------------------------------------------------------------

    def entry(self, item, group: str) -> str | None:
        """Return the first widget an item runs, or None for an empty section."""
        if isinstance(item, _Group):
            if item.row.relevance:
                return f"if_{item.row.name}"
            return self.first_entry(item.children, 0, None, item.row.name)
        widget = _widget(item, group)
        return f"if_{widget}" if item.relevance else widget

    def first_entry(self, items, start, after, group: str):
        for item in items[start:]:
            found = self.entry(item, group)
            if found:
                return found
        return after

    def sequence(self, items, after: str, group: str) -> None:
        for index, item in enumerate(items):
            following = self.first_entry(items, index + 1, after, group)
            if isinstance(item, _Group):
                self.group(item, following)
            else:
                self.row(item, following, group)

    def group(self, item: _Group, after: str) -> None:
        name = item.row.name
        if item.row.relevance:
            x, y = self.place(name)
            self.states += self.condition_splits(
                f"if_{name}",
                item.row.relevance,
                self.first_entry(item.children, 0, after, name),
                after,
                "",
                x=x,
                y=y,
            )
        self.sequence(item.children, after, name)

    def row(self, row: SurveyRow, after: str, group: str) -> None:
        widget = _widget(row, group)
        if row.relevance:
            x, y = self.place(group)
            self.states += self.condition_splits(
                f"if_{widget}", row.relevance, widget, after, group, x=x, y=y
            )
        if row.is_question:
            self.question(row, widget, after, group)
        elif row.type == "note":
            self.note(row, widget, after, group)

    def note(self, row: SurveyRow, widget: str, after: str, group: str) -> None:
        x, y = self.place(group)
        template = row.template.get(self.lang)
        if template and not self.text(row):
            state = send_content(widget, self.sid(template), x=x, y=y)
            state["transitions"] = [
                {"event": "sent", "next": after},
                {"event": "failed", "next": after},
            ]
            self.states.append(state)
        else:
            self.states.append(send(widget, self.text(row), after, x=x, y=y))

    # -- one question --------------------------------------------------------

    def question(self, row, widget, after, group, *, consent=False):
        """Emit the subgraph for one question, ending at `after`."""
        spec = self.spec
        x, y = self.place(group)
        retries = spec.retries_for(row)
        timeout = str(spec.timeout_for(row))
        reminder = spec.reminder_key(row)
        stop_first = spec.listens_for_stop(row)
        stop_again = (
            bool(reminder)
            and spec.settings.stop_words != "never"
            and (stop_first or spec.settings.stop_words == "opening_and_reminders")
        )
        validated = row.is_validated
        options = spec.options(row.list_name, self.lang) if row.is_select else []
        source = (
            f"flow.variables.reply_{widget}"
            if reminder
            else f"widgets.{widget}.inbound.Body"
        )
        reply = f"{{{{{source}}}}}"

        check = f"split_{widget}" if validated else f"store_{widget}"
        landing = f"got_{widget}" if reminder else check
        on_reply = f"stopcheck_{widget}" if stop_first else landing
        on_timeout = f"remind_check_{widget}" if reminder else "mark_no_reply"
        first_wait = str(spec.settings.reminder_after) if reminder else timeout

        def asking(name, reply_to, wait, timeout_to, *, dx=0, dy=0):
            if row.is_select:
                content = self.sid(template_name(spec, widget, self.lang))
                _, columns = numbered_columns(row.label.get(self.lang, ""))
                return ask_content(
                    name,
                    content,
                    reply_to,
                    x=x + dx,
                    y=y + dy,
                    timeout=wait,
                    on_timeout=timeout_to,
                    variables=column_variables(columns),
                )
            return ask(
                name,
                self.text(row),
                reply_to,
                x=x + dx,
                y=y + dy,
                timeout=wait,
                on_timeout=timeout_to,
            )

        states = [asking(widget, on_reply, first_wait, on_timeout)]
        if stop_first:
            states.append(
                self.stop_split(
                    f"stopcheck_{widget}",
                    f"widgets.{widget}.inbound.Body",
                    landing,
                    x=x,
                    y=y + 40,
                )
            )

        if reminder:
            # The question waits until shortly before WhatsApp's 24-hour window
            # closes, then sends the reminder and asks once more. A question
            # re-asked after a nudge comes back here too, so the flag stops a
            # second reminder from going out.
            again = f"{widget}_again"
            states += [
                set_vars(
                    f"got_{widget}",
                    [(f"reply_{widget}", f"{{{{widgets.{widget}.inbound.Body}}}}")],
                    check,
                    x=x,
                    y=y + 80,
                ),
                split(
                    f"remind_check_{widget}",
                    f"{{{{flow.variables.{widget}_reminded}}}}",
                    [("1", "mark_no_reply", "already reminded")],
                    f"mark_remind_{widget}",
                    x=x - 300,
                    y=y,
                ),
                set_vars(
                    f"mark_remind_{widget}",
                    [(f"{widget}_reminded", "1")],
                    f"remind_{widget}",
                    x=x - 300,
                    y=y + 80,
                ),
                send(
                    f"remind_{widget}",
                    self.message(reminder),
                    again,
                    x=x - 300,
                    y=y + 160,
                ),
                asking(
                    again,
                    f"stopcheck_{again}" if stop_again else f"got_{again}",
                    timeout,
                    "mark_no_reply",
                    dx=-300,
                    dy=240,
                ),
                set_vars(
                    f"got_{again}",
                    [(f"reply_{widget}", f"{{{{widgets.{again}.inbound.Body}}}}")],
                    check,
                    x=x - 300,
                    y=y + 320,
                ),
            ]
            if stop_again:
                states.append(
                    self.stop_split(
                        f"stopcheck_{again}",
                        f"widgets.{again}.inbound.Body",
                        f"got_{again}",
                        x=x - 300,
                        y=y + 280,
                    )
                )

        if consent:
            states += self.consent_tail(row, widget, reply, retries, after, x, y)
            self.states += states
            if reminder:
                self.columns.append(
                    (f"{widget}_reminded", f"{{{{flow.variables.{widget}_reminded}}}}")
                )
            return

        if not validated:
            states.append(
                set_vars(
                    f"store_{widget}",
                    [(f"{widget}_status", "answered")],
                    after,
                    x=x,
                    y=y + 160,
                )
            )
            kind = "text"
        else:
            kind = "select" if row.is_select else "number"
            suffix = "code" if row.is_select else "value"
            if row.is_select:
                stored = code_mapping(widget, options, source=source)
            elif row.kind == "decimal":
                # Stored with a point whatever mark was typed, so 3,5 and 3.5 are
                # one value in the data and compare as one in a condition.
                stored = f"{{{{{source} | strip | replace: ',', '.'}}}}"
            else:
                stored = f"{{{{{source} | strip}}}}"
            pattern = (
                answer_pattern(options) if row.is_select else row.resolved_constraint()
            )
            give_up = f"giveup_{widget}"
            refused = f"retry_{widget}" if retries else give_up
            fits = f"range_{widget}" if row.has_range else f"store_{widget}"
            states.append(
                split(
                    check,
                    reply,
                    [(pattern, fits, "a reply that fits")],
                    refused,
                    x=x,
                    y=y + 120,
                    condition="regex",
                )
            )
            if row.has_range:
                states.append(self.range_split(row, widget, source, refused, x, y))
            states += [
                set_vars(
                    f"store_{widget}",
                    [(f"{widget}_status", "answered"), (f"{widget}_{suffix}", stored)],
                    after,
                    x=x,
                    y=y + 200,
                ),
                set_vars(
                    give_up,
                    [(f"{widget}_status", "multierror"), (f"{widget}_{suffix}", "")],
                    after,
                    x=x + 300,
                    y=y + 280,
                ),
            ]
            if retries:
                states += self.retry_loop(row, widget, retries, give_up, x, y)

        self.states += states
        self.answers[(group, row.name)] = _Answer(widget, kind, source)
        if row.publish:
            if row.encrypt:
                self.encrypted.append((f"enc_{widget}", reply))
                self.columns.append(
                    (
                        f"enc_{widget}",
                        f"{{{{widgets.function_encrypt.parsed.enc_{widget}}}}}",
                    )
                )
            else:
                self.columns.append((widget, reply))
            self.columns.append(
                (f"{widget}_status", f"{{{{flow.variables.{widget}_status}}}}")
            )
            if kind != "text":
                suffix = "code" if kind == "select" else "value"
                self.columns.append(
                    (f"{widget}_{suffix}", f"{{{{flow.variables.{widget}_{suffix}}}}}")
                )
            if reminder:
                self.columns.append(
                    (f"{widget}_reminded", f"{{{{flow.variables.{widget}_reminded}}}}")
                )

    def retry_loop(self, row, widget, retries, give_up, x, y, extra=()):
        options = self.spec.options(row.list_name, self.lang) if row.is_select else []
        return [
            set_vars(
                f"retry_{widget}",
                [
                    (
                        f"tries_{widget}",
                        f"{{% assign current = flow.variables.tries_{widget} "
                        "| default: 0 %}{{ current | plus: 1 }}",
                    ),
                    *extra,
                ],
                f"check_{widget}",
                x=x + 300,
                y=y + 120,
            ),
            split(
                f"check_{widget}",
                f"{{{{flow.variables.tries_{widget}}}}}",
                [(str(n), f"error_{widget}") for n in range(1, retries + 1)],
                give_up,
                x=x + 300,
                y=y + 200,
            ),
            send(
                f"error_{widget}",
                self.nudge(row, options),
                widget,
                x=x + 600,
                y=y + 200,
            ),
        ]

    def range_split(self, row, widget, source, refused, x, y):
        """Refuse a number outside the question's minimum and maximum.

        Strict comparisons, which is all Studio has, so the limits themselves
        are taken: a reply is refused only when below the minimum or above the
        maximum.
        """
        number = as_number(f"{{{{{source}}}}}")
        branches = []
        if row.minimum is not None:
            branches.append(
                (str(row.minimum), refused, f"under {row.minimum}", "less_than")
            )
        if row.maximum is not None:
            branches.append(
                (str(row.maximum), refused, f"over {row.maximum}", "greater_than")
            )
        return split(
            f"range_{widget}", number, branches, f"store_{widget}", x=x - 300, y=y + 160
        )

    def nudge(self, row: SurveyRow, options) -> str:
        """Return the text sent before a question is asked again."""
        if row.says_its_range:
            # Said for any reply that does not fit: "five" and "15" both get
            # told which numbers the question takes.
            return self.message(RANGE_MESSAGE).replace("{range}", describe_range(row))
        if row.constraint_message:
            key = row.constraint_message
        elif not row.is_select:
            key = "error_numeric"
        elif row.kind == "select_button" and self.message("error_button"):
            key = "error_button"
        elif positions_are_ambiguous(options):
            key = "error_option_labels"
        else:
            key = "error_option"
        button = row.list_button.get(self.lang) or self.message("list_button")
        return self.message(key).replace("{button}", button)

    # -- the spine -------------------------------------------------------------

    def consent_tail(self, row, widget, reply, retries, after, x, y):
        """Yes enrols, no declines, anything else is unclear and re-asked."""
        choices = self.spec.choice_list(row.list_name)
        by_value = {c.value: c for c in choices}

        def words(choice):
            return [choice.label.get(self.lang, "")] + [
                part.strip()
                for part in choice.typed.get(self.lang, "").split("|")
                if part.strip()
            ]

        no_words = [w for c in choices if c.value != "yes" for w in words(c)]
        give_up = f"giveup_{widget}"
        states = [
            split(
                f"split_{widget}",
                reply,
                [
                    (
                        word_pattern(words(by_value["yes"])),
                        "record_consent",
                        "consented",
                    ),
                    (word_pattern(no_words), "record_declined", "declined"),
                ],
                f"retry_{widget}" if retries else give_up,
                x=x,
                y=y + 120,
                condition="regex",
            ),
            set_vars("record_consent", [("set_consent", "yes")], after, x=x, y=y + 200),
            set_vars(
                "record_declined",
                [("set_consent", "no"), ("outcome", "declined")],
                "finish",
                x=x + 300,
                y=y + 280,
            ),
            # Still unreadable after the re-ask: not enrolled, and not a refusal
            # either. `set_consent` stays `unclear`, and the outcome is a
            # break-off, which is what it was.
            set_vars(
                give_up,
                [("set_consent", "unclear")],
                "mark_no_reply",
                x=x + 300,
                y=y + 360,
            ),
        ]
        if retries:
            states += self.retry_loop(
                row, widget, retries, give_up, x, y, extra=[("set_consent", "unclear")]
            )
        return states

    def build(self) -> dict[str, Any]:
        spec = self.spec
        intro = next((r for r in spec.survey if r.role == "intro"), None)
        consent = spec.consent_row()
        unsolicited = next((r for r in spec.survey if r.role == "unsolicited"), None)

        items = _tree(spec)
        start = self.first_entry(items, 0, "mark_complete", "")
        after_consent = start
        after_intro = "consent" if consent else start

        trigger = [
            {"event": "incomingRequest", "next": "intro" if intro else after_intro},
            {"event": "incomingCall"},
            {"event": "incomingConversationMessage"},
            {"event": "incomingParent"},
        ]
        reply_text = (self.text(unsolicited) if unsolicited else "") or self.message(
            "unsolicited"
        )
        if reply_text:
            trigger.insert(1, {"event": "incomingMessage", "next": "unsolicited_reply"})
            self.states.append(
                send("unsolicited_reply", reply_text, "unsolicited_reply", x=-600, y=0)
            )
        else:
            trigger.insert(1, {"event": "incomingMessage"})
        self.states.insert(
            0,
            {
                "name": "Trigger",
                "type": "trigger",
                "properties": {"offset": {"x": 0, "y": -200}},
                "transitions": trigger,
            },
        )

        if intro is not None:
            self.opener(intro, after_intro)
        if consent is not None:
            self.question(consent, "consent", after_consent, "", consent=True)

        self.sequence(items, "mark_complete", "")
        self.spine()
        self.closings()

        for state in self.states:
            if (
                state["name"].startswith("close_")
                or state["name"] == "unsolicited_reply"
            ):
                state["transitions"] = [{"event": "sent"}, {"event": "failed"}]

        return {
            "description": spec.settings.description.get(self.lang, "")
            or spec.settings.form_title,
            "states": self.states,
            "initial_state": "Trigger",
            "flags": {"allow_concurrent_calls": True},
        }

    def opener(self, row: SurveyRow, after: str) -> None:
        """Send the approved template that opens the 24-hour window, and wait."""
        x, y = self.place("")
        template = row.template.get(self.lang)
        if not template:
            raise BuildError(
                f"the opener {row.name!r} names no template for {self.lang}"
            )
        stop = self.spec.listens_for_stop(row)
        self.states.append(
            ask_content(
                "intro",
                self.sid(template),
                "stopcheck_intro" if stop else after,
                x=x,
                y=y,
                variables=content_variables(row),
                timeout=str(self.spec.timeout_for(row)),
                on_timeout="mark_never_started",
            )
        )
        if stop:
            self.states.append(
                self.stop_split(
                    "stopcheck_intro",
                    "widgets.intro.inbound.Body",
                    after,
                    x=x,
                    y=y + 40,
                )
            )

    def spine(self) -> None:
        """Outcome markers, encryption and the one publish step."""
        functions = self.functions
        publish = functions["publish_widget"]
        y = self.y + 600
        markers = [
            ("mark_complete", [("set_complete", "1"), ("outcome", "complete")]),
            ("mark_no_reply", [("set_no_reply", "1"), ("outcome", "incomplete")]),
            ("mark_never_started", [("set_no_reply", "1"), ("outcome", "unreachable")]),
            ("mark_delivery_failed", [("set_fail", "1"), ("outcome", "undeliverable")]),
        ]
        if any(s["name"].startswith("stopcheck_") for s in self.states):
            markers.append(
                ("mark_optout", [("set_optout", "1"), ("outcome", "optout")])
            )
        for index, (name, pairs) in enumerate(markers):
            self.states.append(set_vars(name, pairs, "finish", x=400 * index, y=y))

        self.states.append(
            set_vars(
                "finish",
                [
                    ("set_reached_finish", "1"),
                    ("enc_status", "ok"),
                    ("final_status", final_status_liquid()),
                ],
                "function_encrypt",
                x=0,
                y=y + 120,
            )
        )
        encrypted = [
            ("enc_p_number_original", "{{contact.channel.address}}"),
            *self.encrypted,
        ]
        self.states.append(
            run_function(
                "function_encrypt",
                functions,
                "encrypt_sid",
                "encrypt_url",
                encrypted,
                publish,
                "mark_encrypt_failed",
                x=0,
                y=y + 240,
            )
        )
        self.states.append(
            set_vars(
                "mark_encrypt_failed",
                [
                    ("enc_status", "encrypt_failed"),
                    ("final_status", ENCRYPTION_FAILED_STATUS),
                ],
                publish,
                x=-500,
                y=y + 300,
            )
        )

        columns = [("caseid", "{{flow.data.caseid}}"), ("lang", self.lang)]
        columns += [(p, f"{{{{flow.data.{p}}}}}") for p in self.condition_preloads]
        columns += [
            (
                "enc_p_number_original",
                "{{widgets.function_encrypt.parsed.enc_p_number_original}}",
            ),
            ("enc_status", "{{flow.variables.enc_status}}"),
            ("set_consent", "{{flow.variables.set_consent}}"),
            ("set_complete", "{{flow.variables.set_complete}}"),
            ("set_no_reply", "{{flow.variables.set_no_reply}}"),
            ("set_fail", "{{flow.variables.set_fail}}"),
            ("outcome", "{{flow.variables.outcome}}"),
            ("final_status", "{{flow.variables.final_status}}"),
            ("execution_sid", "{{flow.sid}}"),
            ("sent_at", "{{flow.data.sent_at}}"),
            *self.columns,
        ]
        self.states.append(
            run_function(
                publish,
                functions,
                "publish_sid",
                "publish_url",
                columns,
                "split_closing",
                "record_publish_failure",
                x=0,
                y=y + 360,
            )
        )
        self.states.append(
            {
                "name": "record_publish_failure",
                "type": "set-variables",
                "properties": {
                    "offset": {"x": -1000, "y": y + 480},
                    # The flag only: no row was written, so the execution context
                    # is the one record left of what happened, and `rtt fetch`
                    # reads it.
                    "variables": [{"key": "set_publish_failed", "value": "1"}],
                },
                "transitions": [{"event": "next"}],
            }
        )
        self.y = y + 480

    def closings(self) -> None:
        """One closing per outcome the spec writes one for; silence otherwise."""
        y = self.y + 120
        branches = []
        closes = []
        for row in self.spec.survey:
            if row.role != "close":
                continue
            parsed = parse_condition(row.relevance)
            if parsed is None or parsed[0] != "outcome":
                raise BuildError(
                    f"closing {row.name!r} needs a condition like ${{outcome}}='complete'"
                )
            outcome = parsed[2]
            if outcome in _SILENT_OUTCOMES:
                continue
            name = f"close_{outcome}"
            branches.append((outcome, name))
            closes.append((row, name, outcome))
        self.states.append(
            split(
                "split_closing",
                "{{flow.variables.outcome}}",
                branches,
                "end_without_message",
                x=0,
                y=y,
            )
        )
        for index, (row, name, outcome) in enumerate(closes):
            x = 450 * index - 900
            template = row.template.get(self.lang)
            if outcome == "unreachable" or (template and not self.text(row)):
                if not template:
                    raise BuildError(
                        f"closing {row.name!r} goes to people who never replied, "
                        "so it needs an approved template"
                    )
                self.states.append(
                    send_content(
                        name,
                        self.sid(template),
                        x=x,
                        y=y + 120,
                        variables=content_variables(row),
                    )
                )
            else:
                self.states.append(send(name, self.text(row), name, x=x, y=y + 120))
        self.states.append(
            {
                "name": "end_without_message",
                "type": "set-variables",
                "properties": {
                    "offset": {"x": 1400, "y": y + 120},
                    "variables": [{"key": "set_closed_silently", "value": "1"}],
                },
                "transitions": [{"event": "next"}],
            }
        )


def compile_spec(
    spec: Spec, lang: str, content_sids: dict[str, str], functions: dict[str, str]
) -> dict[str, Any]:
    """Compile one language of a spec into a Studio flow definition.

    Args:
        spec: The instrument. It must pass :func:`~requests_to_twilio.spec.check_spec`.
        lang: One of the spec's languages.
        content_sids: Friendly name to HX SID for every template in
            :func:`required_templates`.
        functions: Functions coordinates, as
            :func:`~requests_to_twilio.widgets.resolve_functions` returns them.

    Returns:
        The flow definition, ready for ``rtt flow check`` and ``rtt flow deploy``.

    Raises:
        BuildError: If the spec fails its checks, the language is unknown, or a
            content SID is missing.

    """
    if lang not in spec.settings.languages:
        raise BuildError(
            f"{lang!r} is not one of this survey's languages: "
            + ", ".join(spec.settings.languages)
        )
    problems = check_spec(spec)
    if problems:
        raise BuildError(
            "the spec does not pass its checks:\n"
            + "\n".join(f"  {p}" for p in problems)
        )
    try:
        return _Compiler(spec, lang, content_sids, functions).build()
    except SpecError as exc:
        raise BuildError(str(exc)) from exc
