"""Turn a set of answer options into the three things that must agree about them.

A closed question in a WhatsApp flow is not one artifact but three, and they are
generated separately:

1. the **message** the respondent sees - a list picker's items, or a numbered
   fallback body;
2. the **split condition** that decides whether a reply counts as an answer;
3. the **code mapping** that decides *which* answer it was.

If any two disagree, the failure is silent and asymmetric. A split more tolerant
than the mapping records somebody as having answered while their answer codes as
``other``. A split stricter than the message strands a respondent who tapped a
real option. Neither shows up in the editor; both show up in the data, months
later, as a column that is half labels and half nulls.

So all three are built from one option table by the functions here, and
:func:`normalise_reply` exists as the Python twin of the Liquid filter chain in
:func:`code_mapping` specifically so a test can push a reply through both and
compare. That pairing is the point of the module.

These lived in ``scripts/build_data_use_demo.py`` while the demo flow was the
only caller. They are here because the survey spec validator needs the same
judgements - a spec has to be told an option is unreachable before it is
compiled, not after a round - and a script is not importable.
"""

from __future__ import annotations

import re

#: Codepoint ranges that make a string risky to compare byte-for-byte: emoji and
#: pictographs, plus the variation selectors and zero-width joiner that let two
#: visually identical strings differ. Accented Latin letters are deliberately not
#: here - Spanish needs them and they compare fine.
_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
    (0xFE00, 0xFE0F),
    (0x200D, 0x200D),
    (0x2B00, 0x2BFF),
)


def has_emoji(text: str) -> bool:
    """Report whether the string holds a character unsafe to match literally."""
    return any(
        any(low <= ord(char) <= high for low, high in _EMOJI_RANGES) for char in text
    )


#: Characters that mean something to every regex flavour and so must be escaped
#: in a literal. Deliberately not Python's `re.escape`, which also escapes a
#: space as "\ " - valid in Python, an error in a JavaScript unicode-mode regex,
#: and we do not control which engine Studio runs.
_REGEX_SPECIAL = set("\\^$.|?*+()[]{}")


def escape_literal(text: str) -> str:
    """Escape a string so a regex matches it literally."""
    return "".join("\\" + char if char in _REGEX_SPECIAL else char for char in text)


#: A label that opens with a number, e.g. "0 projects", "1-2 times", "5 - Very
#: satisfied". The captured digits are what a respondent typing that number
#: would mean.
_LEADING_NUMBER = re.compile(r"^\s*(\d+)")


def positions_are_ambiguous(options) -> bool:
    """Whether accepting a bare typed digit would miscode an answer.

    Options are normally matched three ways: the row id, the label, and the
    option's position typed as a digit. That last one is only safe while the
    two readings of a number cannot disagree.

    Take a frequency scale reading ``0 projects / 1 project / 2-3 projects``.
    A respondent who means *one project* types ``1``. As a position, ``1`` is
    the first option - ``0 projects``. The answer is recorded, the status says
    answered, and it is off by one for exactly the respondents who typed rather
    than tapped. Nothing anywhere reports a problem.

    The test is a *mismatch*, not merely the presence of a number: a Likert
    running ``1 - Very dissatisfied`` … ``5 - Very satisfied`` puts label 5 at
    position 5, so both readings agree and the digit stays useful. Only when
    some label's own number differs from where it sits does the position
    alternative get dropped - from the split regex, from the Liquid that codes
    the reply, and from the Python twin that tests them. A bare digit then
    matches nothing and the respondent is asked again, which is the right
    outcome for an input that genuinely has two readings.
    """
    for index, option in enumerate(options, start=1):
        found = _LEADING_NUMBER.match(normalise_reply(option[1]))
        if found and int(found.group(1)) != index:
            return True
    return False


def answer_pattern(options) -> str:
    """Build the regex that recognises any valid answer to a list question.

    Args:
        options: ``(id, item, description)`` triples in display order.

    Returns:
        A Studio `regex` condition value.

    Accepts the tapped item label *or* its position typed as a digit, because a
    respondent who ignores the menu and writes "3" has still answered. Storing
    only one of the two forms is how a flow ends up with an answer column that
    is half labels and half numbers.

    Regex rather than `matches_any_of` for one structural reason: that predicate
    takes its alternatives as a single comma-delimited string, so a comma inside
    an option label silently becomes two alternatives that never match. Here a
    comma is just a character. It also lets the digit form tolerate the
    punctuation people actually type - "1.", "1)", "(1)".

    Studio anchors the pattern to the whole string, so the alternation must be
    wrapped: an unwrapped `a|b` can bind as `(^a)|(b$)` and match "xxb".

    """
    accept_positions = not positions_are_ambiguous(options)
    alternatives: list[str] = []
    for index, option in enumerate(options, start=1):
        # The id first, because that is what a tapped list row actually
        # returns. The label second, because a tapped quick-reply button
        # returns *its* title instead - the two interactive types disagree.
        # The position last, for anyone who ignores the menu and types - unless
        # the labels are numeric, in which case a bare digit has two readings
        # and is refused rather than guessed at.
        alternatives.append(escape_literal(option[0]))
        alternatives.append(escape_literal(option[1]))
        if accept_positions:
            alternatives.append(rf"\(?{index}[.)]?")
    return r"(?:\s*(?:" + "|".join(alternatives) + r")\s*)"


def word_pattern(words) -> str:
    """Build a regex matching any of these literal words, tolerating padding."""
    return (
        r"(?:\s*(?:"
        + "|".join(escape_literal(word) for word in words if word)
        + r")\s*)"
    )


#: Punctuation people add around a typed option number: "1.", "1)", "(1)".
#: Stripped before coding so the split and the code agree about what counts.
_STRIPPED_PUNCTUATION = (".", ")", "(")


def normalise_reply(reply: str) -> str:
    """Reduce a reply to the form the code mapping compares against.

    This is the Python twin of the Liquid filter chain in :func:`code_mapping`.
    Both exist so the two can be checked against each other: the split decides
    whether a reply counts as an answer, the mapping decides which answer it
    was, and if they disagree a respondent gets credited with answering while
    their answer codes as `other`.
    """
    text = reply.strip().casefold()
    for char in _STRIPPED_PUNCTUATION:
        text = text.replace(char, "")
    return text.strip()


def option_code(option, index: int) -> str:
    """Return the value stored for an option: its own code, or its position.

    An option may carry an explicit code as a fourth element. That exists for
    the answers that are *offered on a scale but are not points on it* - "Prefer
    not to say", "Don't know". Left to their position they would code as a 6 on
    a 5-point item and be silently averaged in, which is the kind of error that
    survives all the way into a published mean.
    """
    return str(option[3]) if len(option) > 3 else str(index)


def expected_code(options, reply: str) -> str:
    """Return the code the flow will store for this reply, or "other"."""
    normalised = normalise_reply(reply)
    accept_positions = not positions_are_ambiguous(options)
    for index, option in enumerate(options, start=1):
        accepted = [normalise_reply(option[0]), normalise_reply(option[1])]
        if accept_positions:
            accepted.append(str(index))
        if normalised in accepted:
            return option_code(option, index)
    return "other"


def code_mapping(widget: str, options) -> str:
    """Build Liquid that normalises a reply to its option number.

    The stored answer must not depend on whether the respondent tapped or
    typed. A tap puts the item's label in the message body; a typed reply puts
    a digit there. This collapses both to the option's position, and leaves
    anything else as `other` rather than silently coding it as a real answer.

    The filter chain mirrors :func:`normalise_reply` exactly, because the split
    condition is deliberately tolerant of casing and trailing punctuation - and
    a mapping that were stricter would quietly code those replies as `other`
    while the split had already recorded them as answered.
    """
    removals = "".join(f' | replace: "{char}", ""' for char in _STRIPPED_PUNCTUATION)
    accept_positions = not positions_are_ambiguous(options)
    clauses = []
    for index, option in enumerate(options, start=1):
        alternatives = [
            f'"{normalise_reply(option[0])}"',
            f'"{normalise_reply(option[1])}"',
        ]
        if accept_positions:
            alternatives.append(f'"{index}"')
        clauses.append(
            f"{{% when {' or '.join(alternatives)} %}}{option_code(option, index)}"
        )
    return (
        f"{{% assign reply = widgets.{widget}.inbound.Body "
        f"| strip | downcase{removals} | strip %}}"
        f"{{% case reply %}}{''.join(clauses)}"
        "{% else %}other{% endcase %}"
    )
