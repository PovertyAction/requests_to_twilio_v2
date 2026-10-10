"""The survey builder: a single offline HTML page that edits a spec's JSON.

The page lives next to this module as ``survey_builder.html`` and is opened from
disk - no server, no hosting, and no Twilio credentials, because it never talks
to Twilio. It reads and writes the same canonical JSON as ``rtt survey convert``,
and ``rtt survey check`` stays the authoritative check: the page only shows the
limits a respondent's phone enforces, while you type.

Those limits, the type and role vocabularies, and the starter instrument are
facts the Python side owns. The page carries a copy of them in one JSON block,
written by :func:`write_contract` and compared by a test, so the two cannot
drift apart without a failure - the same arrangement as ``sample_template.xlsx``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .answers import _EMOJI_RANGES
from .flows import (
    LIST_BUTTON_CHARS,
    MAX_LIST_ITEMS,
    MAX_SESSION_BUTTONS,
    TEXT_LIMITS,
)
from .outcomes import OUTCOMES
from .spec import (
    CONSTRAINED_TYPES,
    DEFAULT_CONSTRAINTS,
    DEFAULT_REMINDER_AFTER,
    QUESTION_TYPES,
    REMINDER_MESSAGE,
    ROLES,
    SCOPE_NOTE,
    SESSION_WINDOW_SECONDS,
    STOP_WORD_SCOPES,
    STRUCTURE_TYPES,
    TYPE_ALIASES,
    UNIVERSAL_STOP_WORD,
    spec_to_dict,
    starter_spec,
)

BUILDER_HTML = Path(__file__).with_name("survey_builder.html")

#: The block the page reads its contract from. Everything between the two
#: markers is generated; everything else in the file is hand-written.
_CONTRACT_BLOCK = re.compile(
    r'(<script type="application/json" id="rtt-contract">\n)(.*?)(\n</script>)',
    re.DOTALL,
)


def contract() -> dict[str, Any]:
    """Return the facts the builder page must agree with the Python side on."""
    limits = {f"{kind.split('/')[1]}:{name}": cap for kind, name, cap in TEXT_LIMITS}
    limits.update(
        {
            "list_button": LIST_BUTTON_CHARS,
            "max_list_items": MAX_LIST_ITEMS,
            "max_session_buttons": MAX_SESSION_BUTTONS,
        }
    )
    return {
        "limits": limits,
        "question_types": sorted(QUESTION_TYPES),
        "constrained_types": sorted(CONSTRAINED_TYPES),
        "structure_types": sorted(STRUCTURE_TYPES),
        "type_aliases": TYPE_ALIASES,
        "roles": sorted(ROLES),
        "outcomes": list(OUTCOMES),
        "default_constraints": DEFAULT_CONSTRAINTS,
        "stop_word_scopes": list(STOP_WORD_SCOPES),
        "universal_stop_word": UNIVERSAL_STOP_WORD,
        "reminder_message": REMINDER_MESSAGE,
        "default_reminder_after": DEFAULT_REMINDER_AFTER,
        "session_window": SESSION_WINDOW_SECONDS,
        "emoji_ranges": [list(pair) for pair in _EMOJI_RANGES],
        "scope_note": SCOPE_NOTE,
        "starter": spec_to_dict(starter_spec("en")),
    }


def render_contract() -> str:
    """Return the contract exactly as it is written into the page."""
    return json.dumps(contract(), indent=2, ensure_ascii=False)


def embedded_contract(html: str) -> str:
    """Return the contract block a page carries.

    Raises:
        ValueError: If the page has no contract block.

    """
    match = _CONTRACT_BLOCK.search(html)
    if match is None:
        raise ValueError("survey_builder.html has no rtt-contract block")
    return match.group(2)


def write_contract(path: Path = BUILDER_HTML) -> Path:
    """Rewrite the page's contract block from the Python side."""
    html = path.read_text(encoding="utf-8")
    embedded_contract(html)  # fail loud on a page without the block
    updated = _CONTRACT_BLOCK.sub(
        lambda m: m.group(1) + render_contract() + m.group(3), html, count=1
    )
    path.write_text(updated, encoding="utf-8", newline="\n")
    return path
