"""Pull Twilio Studio flow definitions into the repository.

A Studio flow *is* the survey instrument: its questions, their wording, the
branching between them. Built in the web editor it lives only in Twilio, where
nobody can diff it, review it, or say what a respondent was actually asked six
months ago. Flow revision numbers in the hundreds or thousands are normal, and
none of those edits are recorded anywhere a researcher can see.

The definition is plain JSON over the API, so it can be pulled, diffed and
reviewed. `flows/` is gitignored: a pulled definition is a snapshot of state
that lives in Twilio and can be re-fetched at any time, and the files are large
enough to bury a real change in a diff.

Definitions are still scanned for credentials before being written. A flow can
legitimately reference service SIDs, but a Function widget's parameters are
free-form and people do paste keys into them; a pulled file gets shared, pasted
into tickets and read by other tools even when it is not committed. This
repository has already published one live private key.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .log import get_logger

logger = get_logger()

#: Patterns that should never reach a committed flow definition. Twilio SIDs are
#: deliberately absent: they are identifiers, not secrets, and flagging them
#: would train people to ignore this warning.
_SECRET_PATTERNS: list[tuple[str, str]] = [
    ("PEM private key", r"BEGIN [A-Z ]*PRIVATE KEY"),
    ("Google API key", r"AIza[0-9A-Za-z_\-]{35}"),
    ("AWS access key", r"AKIA[0-9A-Z]{16}"),
    ("service account JSON", r"\"type\"\s*:\s*\"service_account\""),
    ("bearer token", r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
    (
        "secret-looking parameter",
        r"(?i)\"(secret|password|passwd|api_?key|auth_?token)\"\s*:\s*\"[^\"]{8,}\"",
    ),
    # Twilio SIDs are a two-letter prefix followed by exactly 32 hex characters,
    # so the boundaries here must reject any adjacent alphanumeric - otherwise
    # every service and function SID in a flow trips the scan, and a warning
    # that always fires is a warning nobody reads.
    (
        "32-char hex (possible auth token)",
        r"(?<![0-9A-Za-z])[0-9a-f]{32}(?![0-9A-Za-z])",
    ),
]

#: Widget types that carry respondent-facing question text.
QUESTION_TYPES = frozenset({"send-and-wait-for-reply"})

#: Matches ``{{flow.data.foo}}`` - a value the flow expects to be preloaded from
#: the sample file at launch, the Studio equivalent of SurveyCTO preloads.
_PRELOAD_PATTERN = re.compile(r"\{\{\s*flow\.data\.([A-Za-z0-9_]+)\s*\}\}")


class FlowError(Exception):
    """Raised when a flow cannot be retrieved or is unsafe to write."""


def list_flows(client: Client, limit: int = 200) -> list[Any]:
    """Return the account's Studio flows, newest activity first."""
    try:
        flows = client.studio.v2.flows.list(limit=limit)
    except TwilioRestException as exc:
        raise FlowError(
            f"Could not list flows: HTTP {exc.status} (code {exc.code}): {exc.msg}"
        ) from exc
    # date_updated can be null on very old flows, so sort defensively.
    return sorted(
        flows, key=lambda f: (f.date_updated is not None, f.date_updated), reverse=True
    )


def resolve_flow(client: Client, identifier: str) -> Any:
    """Find a flow by SID or by friendly name.

    Args:
        client: An authenticated Twilio client.
        identifier: A flow SID starting ``FW``, or an exact friendly name.

    Returns:
        The flow resource.

    Raises:
        FlowError: If nothing matches, or a name matches more than one flow.

    """
    if identifier.startswith("FW"):
        try:
            return client.studio.v2.flows(identifier).fetch()
        except TwilioRestException as exc:
            raise FlowError(
                f"No flow {identifier}: HTTP {exc.status} (code {exc.code}): {exc.msg}"
            ) from exc

    matches = [f for f in list_flows(client) if f.friendly_name == identifier]
    if not matches:
        raise FlowError(
            f"No flow named {identifier!r}. Run `rtt flow list` to see what exists."
        )
    if len(matches) > 1:
        sids = ", ".join(f.sid for f in matches)
        raise FlowError(
            f"{len(matches)} flows are named {identifier!r} ({sids}). Use the SID."
        )

    # The list endpoint does not populate `definition`; only a fetch by SID
    # does. Returning the list instance directly would hand back a flow whose
    # definition is None, which fails much later and confusingly.
    return resolve_flow(client, matches[0].sid)


def scan_for_secrets(definition: dict) -> list[str]:
    """Look for credential-shaped strings in a flow definition.

    Args:
        definition: The flow's JSON definition.

    Returns:
        Human-readable descriptions of anything suspicious, empty if clean.

    """
    raw = json.dumps(definition)
    findings = []
    for label, pattern in _SECRET_PATTERNS:
        hits = set(re.findall(pattern, raw))
        if hits:
            findings.append(f"{label}: {len(hits)} occurrence(s)")
    return findings


def summarize(definition: dict) -> dict[str, Any]:
    """Describe a flow: widget counts, questions, and where it publishes.

    Args:
        definition: The flow's JSON definition.

    Returns:
        A summary with widget type counts, the question widgets, the function
        widgets, and whether anything looks like an encryption step.

    """
    states = definition.get("states", [])

    counts: dict[str, int] = {}
    questions: list[dict[str, str]] = []
    functions: list[dict[str, Any]] = []

    for state in states:
        kind = state.get("type", "unknown")
        counts[kind] = counts.get(kind, 0) + 1

        if kind in QUESTION_TYPES:
            body = (state.get("properties", {}).get("body") or "").strip()
            questions.append({"name": state.get("name", ""), "body": body})

        if kind == "run-function":
            props = state.get("properties", {})
            functions.append(
                {
                    "name": state.get("name", ""),
                    "parameters": [p.get("key") for p in props.get("parameters", [])],
                }
            )

    # A flow is "encrypting" if some function widget looks like the encryption
    # step, judged by name. Deliberately loose: the point is to prompt a human
    # to check, not to certify anything.
    encrypting = any("encrypt" in (f["name"] or "").lower() for f in functions)

    return {
        "widget_counts": counts,
        "questions": questions,
        "functions": functions,
        "encrypting": encrypting,
    }


def _is_publish_widget(state: dict) -> bool:
    """Whether a widget looks like the step that writes a row to Google Sheets."""
    name = (state.get("name") or "").lower()
    return state.get("type") == "run-function" and (
        "gsheet" in name or name.startswith("pub")
    )


def unpublished_paths(definition: dict) -> list[tuple[str, str, str]]:
    """Find break-off paths that never reach the publish widget.

    Args:
        definition: The flow's JSON definition.

    Returns:
        ``(widget, event, destination)`` for every ``timeout`` or
        ``deliveryFailure`` transition from which no publish widget is
        reachable.

    Publishing is what makes the Google Sheet a live dashboard: each execution
    appends its row the moment that respondent's flow reaches the widget, so the
    sheet fills as submissions arrive rather than at the end of a round.

    Flows carry exactly one publish widget and route every terminal path through
    it - complete, multierror, no_reply and delivery failure alike - so that a
    row exists whatever happened. A path that misses it produces no row at all,
    and a respondent who broke off becomes indistinguishable from one who was
    never contacted. That is the difference between measured attrition and
    missing data.

    """
    states = definition.get("states", [])
    publishers = {s["name"] for s in states if _is_publish_widget(s)}
    if not publishers:
        return []

    # Walk edges backwards from the publish widgets to find everything that can
    # still reach one.
    incoming: dict[str, list[str]] = {}
    for state in states:
        for transition in state.get("transitions", []):
            destination = transition.get("next")
            if destination:
                incoming.setdefault(destination, []).append(state["name"])

    reaching = set(publishers)
    queue = list(publishers)
    while queue:
        node = queue.pop()
        for parent in incoming.get(node, []):
            if parent not in reaching:
                reaching.add(parent)
                queue.append(parent)

    stranded = []
    for state in states:
        for transition in state.get("transitions", []):
            event = transition.get("event")
            if event not in ("timeout", "deliveryFailure"):
                continue
            destination = transition.get("next")
            if destination is None:
                stranded.append((state["name"], event, "<dead end>"))
            elif destination not in reaching:
                stranded.append((state["name"], event, destination))
    return stranded


def evaluate_condition(kind: str, value: str, reply: str) -> bool:
    """Decide a single Studio split condition the way Studio decides it.

    Args:
        kind: The condition type, e.g. ``equal_to`` or ``regex``.
        value: The condition's configured value.
        reply: The text being tested.

    Returns:
        Whether the condition matches. Unknown condition types return False.

    Studio's documented semantics, which are not obvious and matter here:

    * Conditions are **case-insensitive** and **trim leading and trailing
      whitespace** from the value being tested. So neither casing nor a stray
      space is the reason an option fails to match.
    * ``matches_any_of`` takes its alternatives as **one comma-delimited
      string**. A comma inside an option label therefore splits it into two
      alternatives that can never match - silently.
    * ``regex`` is written without slashes, is case-insensitive, and **must
      match the entire string**. Because the anchoring is applied around
      whatever pattern you supply, bare alternation (``a|b``) can bind as
      ``(^a)|(b$)``; patterns must wrap themselves in ``(?:...)``.

    This exists so option routing can be *executed* in a test rather than read
    and hoped about. A condition that looks right and matches nothing is the
    same defect as a break-off that publishes no row: invisible in the editor,
    obvious only in the data.

    """
    reply = reply.strip()
    folded = reply.casefold()

    if kind == "equal_to":
        return folded == value.strip().casefold()
    if kind == "not_equal_to":
        return folded != value.strip().casefold()
    if kind == "matches_any_of":
        return folded in {part.strip().casefold() for part in value.split(",")}
    if kind == "does_not_match_any_of":
        return folded not in {part.strip().casefold() for part in value.split(",")}
    if kind == "regex":
        try:
            return re.fullmatch(value, reply, re.IGNORECASE | re.DOTALL) is not None
        except re.error:
            return False
    if kind == "contains":
        return value.strip().casefold() in folded
    if kind == "starts_with":
        return folded.startswith(value.strip().casefold())
    if kind == "is_blank":
        return not folded
    if kind == "is_not_blank":
        return bool(folded)
    return False


def route_split(state: dict, reply: str) -> str | None:
    """Return the widget a split sends this reply to.

    Args:
        state: A ``split-based-on`` widget definition.
        reply: The text being tested.

    Returns:
        The destination widget name, or None if the split dead-ends.

    Match transitions are evaluated in order, as Studio does, and the noMatch
    branch is the fallback.

    """
    fallback = None
    for transition in state.get("transitions", []):
        if transition.get("event") == "noMatch":
            fallback = transition.get("next")

    for transition in state.get("transitions", []):
        if transition.get("event") != "match":
            continue
        conditions = transition.get("conditions") or []
        if conditions and all(
            evaluate_condition(c.get("type", ""), c.get("value", ""), reply)
            for c in conditions
        ):
            return transition.get("next")
    return fallback


def unmatchable_conditions(definition: dict) -> list[str]:
    """Find split conditions that cannot match anything.

    Args:
        definition: The flow's JSON definition.

    Returns:
        Descriptions of conditions that are broken rather than merely unmet.

    Two failures are worth catching, because both look fine on the canvas and
    simply route every respondent down the noMatch branch:

    * A regex that does not compile. Studio does not reject it.
    * A ``matches_any_of`` with an empty alternative, which is what a trailing
      comma or a comma inside an option label leaves behind.

    """
    problems = []
    for state in definition.get("states", []):
        if state.get("type") != "split-based-on":
            continue
        for transition in state.get("transitions", []):
            for condition in transition.get("conditions") or []:
                kind = condition.get("type", "")
                value = condition.get("value", "")
                where = f"{state.get('name')} / {condition.get('friendly_name', kind)}"

                if kind == "regex":
                    try:
                        re.compile(value)
                    except re.error as exc:
                        problems.append(f"{where}: regex does not compile ({exc})")
                elif kind in ("matches_any_of", "does_not_match_any_of") and any(
                    not part.strip() for part in value.split(",")
                ):
                    problems.append(
                        f"{where}: empty alternative in a comma-delimited list, "
                        "usually a comma inside an option label"
                    )
    return problems


#: Widget types that put a message on the wire.
_SENDING_TYPES = frozenset({"send-message", "send-and-wait-for-reply"})


def opening_sends(definition: dict) -> list[str]:
    """Find the first message an API-launched execution puts on the wire.

    Args:
        definition: The flow's JSON definition.

    Returns:
        Widget names that can be the first message sent when the flow is
        started over the REST API. Usually one; more if a split precedes it.

    `rtt launch` starts a business-initiated conversation: nobody has messaged
    us, so the 24-hour customer service window is shut. WhatsApp only accepts an
    approved template as the message that opens it. A free-form body there fails
    with error 63016 for every respondent in the round, and it fails at the very
    first message, so nothing else in the flow ever runs.

    Only the path from `incomingRequest` is walked. The `incomingMessage` path
    means the respondent wrote first, which opens the window by itself.

    """
    states = {s.get("name"): s for s in definition.get("states", [])}
    trigger = next(
        (s for s in states.values() if s.get("type") == "trigger"),
        None,
    )
    if trigger is None:
        return []

    start = [
        t.get("next")
        for t in trigger.get("transitions", [])
        if t.get("event") == "incomingRequest" and t.get("next")
    ]

    first: list[str] = []
    seen: set[str] = set()
    queue = list(start)
    while queue:
        name = queue.pop(0)
        if name in seen or name not in states:
            continue
        seen.add(name)
        state = states[name]

        if state.get("type") in _SENDING_TYPES:
            # This one sends. Stop here - anything after it is either a reply
            # (window open) or unreachable until this message succeeds.
            first.append(name)
            continue

        # Everything else - splits, variables, functions - is silent, so keep
        # walking until an actual message is found.
        for transition in state.get("transitions", []):
            if transition.get("next"):
                queue.append(transition["next"])
    return first


#: Suffixes that mark a column as the status belonging to the answer before it.
STATUS_SUFFIXES = ("_status", "_err", "_error", "_state", "_outcome")


def published_columns(definition: dict) -> list[tuple[str, str]]:
    """Return the publish widget's columns in order, as (key, source).

    Args:
        definition: The flow's JSON definition.

    Returns:
        ``(column_name, source)`` pairs in the order they are published, where
        source is one of ``answer``, ``variable``, ``preload``, ``encrypted``
        or ``other``.

    """
    for state in definition.get("states", []):
        if not _is_publish_widget(state):
            continue

        columns = []
        for parameter in state.get("properties", {}).get("parameters", []):
            key = parameter.get("key") or ""
            value = str(parameter.get("value") or "")

            if "encrypt" in value or "encriptador" in value:
                source = "encrypted"
            elif ".inbound." in value:
                source = "answer"
            elif "flow.variables." in value:
                source = "variable"
            elif "flow.data." in value:
                source = "preload"
            else:
                source = "other"
            columns.append((key, source))
        return columns
    return []


def unpaired_answers(definition: dict) -> list[str]:
    """Find answer columns published without an adjacent status column.

    Args:
        definition: The flow's JSON definition.

    Returns:
        Answer column names that have no companion status column.

    A blank answer cell is ambiguous on its own: the respondent may have
    stopped replying, the message may have failed to deliver, or the question
    may never have been asked because of branching. Those mean different things
    and the analyst cannot tell them apart from the answer alone - the last
    non-empty column only says where a respondent *stopped*, not why.

    Pairing each answer with its own status resolves it, and locates the
    drop-off at the question rather than at the section.

    """
    columns = published_columns(definition)
    names = [key for key, _ in columns]

    unpaired = []
    for index, (key, source) in enumerate(columns):
        if source != "answer":
            continue

        # A status either carries a recognised suffix on this column's name, or
        # sits immediately after it.
        expected = {f"{key}{suffix}" for suffix in STATUS_SUFFIXES}
        if expected & set(names):
            continue

        following = names[index + 1] if index + 1 < len(names) else ""
        if following.startswith(key) and following != key:
            continue

        unpaired.append(key)
    return unpaired


#: Variables whose presence in the publish payload means a final status is
#: recorded. Section-scoped names (set_no_reply_dem) count via the prefix.
_FINAL_STATUS_PREFIXES = (
    "set_complete",
    "set_no_reply",
    "set_fail",
    "set_multierror",
    "set_consent",
    "set_survey_fail",
)


@dataclass
class Finding:
    """One problem found while checking a flow."""

    severity: str  # "error" or "warning"
    code: str
    summary: str
    detail: list[str] = field(default_factory=list)


#: Content types that cannot be the first message of a business-initiated
#: conversation. They are perfectly good replies; they just cannot open the door.
_CANNOT_OPEN_SESSION = frozenset({"twilio/list-picker", "twilio/location"})

#: Hard ceiling on a list picker's options. Twilio rejects more than this.
MAX_LIST_ITEMS = 10

#: Character limits Twilio documents for interactive content, as
#: ``(content type, field, limit)``. These are what a respondent's phone will
#: actually render; past them the create call fails with a generic error that
#: does not say which string was too long.
#:
#: The limits shape question *design*, not just implementation - 24 characters
#: is shorter than most people's first draft of an answer option, and it is the
#: reason a standard Likert midpoint has to be reworded. Check them before
#: writing the instrument, not after.
_TEXT_LIMITS: tuple[tuple[str, str, int], ...] = (
    ("twilio/list-picker", "body", 1024),
    ("twilio/list-picker", "item", 24),
    ("twilio/list-picker", "description", 72),
    ("twilio/list-picker", "id", 200),
    ("twilio/quick-reply", "title", 25),
)

#: Meta's limit on the button that opens a list. Twilio's documentation gives no
#: maximum for this field, so this is the WhatsApp platform limit rather than a
#: documented Twilio one - hence a warning, not an error.
LIST_BUTTON_CHARS = 20


def overlong_content_text(
    definition: dict, content_types: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Find interactive text that exceeds what WhatsApp will render.

    Args:
        definition: The flow's JSON definition.
        content_types: Content SID to its ``types`` mapping.

    Returns:
        ``(errors, warnings)`` - errors for the limits Twilio documents,
        warnings for the one it does not.

    """
    errors: list[str] = []
    warnings: list[str] = []

    for state in definition.get("states", []):
        sid = state.get("properties", {}).get("content_sid")
        types = content_types.get(sid) if sid else None
        if not isinstance(types, dict):
            continue
        name = state.get("name", "")

        for type_name, field_name, limit in _TEXT_LIMITS:
            config = types.get(type_name)
            if not isinstance(config, dict):
                continue

            # Top-level strings, then the same field inside items/actions.
            candidates: list[tuple[str, Any]] = [(field_name, config.get(field_name))]
            for collection in ("items", "actions"):
                entries = config.get(collection)
                if isinstance(entries, list):
                    candidates += [
                        (f"{collection}[{i}].{field_name}", entry.get(field_name))
                        for i, entry in enumerate(entries)
                        if isinstance(entry, dict)
                    ]

            for where, text in candidates:
                if isinstance(text, str) and len(text) > limit:
                    errors.append(
                        f"{name}: {type_name} {where} is {len(text)} chars, "
                        f"the maximum is {limit}: {text[:40]!r}"
                    )

        picker = types.get("twilio/list-picker")
        if isinstance(picker, dict):
            button = picker.get("button")
            if isinstance(button, str) and len(button) > LIST_BUTTON_CHARS:
                warnings.append(
                    f"{name}: list button is {len(button)} chars; WhatsApp "
                    f"allows about {LIST_BUTTON_CHARS} and Twilio documents no "
                    f"limit, so this may be truncated: {button!r}"
                )
    return errors, warnings


#: Ceiling on quick-reply buttons for the way this repo uses them. Twilio allows
#: up to 10, but only on a template Meta has approved; sent in session without
#: approval, WhatsApp permits 3. Every question template here is deliberately
#: never submitted, so 3 is the real limit for anything after the opener. Past
#: it, WhatsApp does not truncate politely - the send fails.
MAX_SESSION_BUTTONS = 3


def oversized_option_sets(
    definition: dict, content_types: dict[str, dict[str, Any]]
) -> list[str]:
    """Find interactive messages with more options than WhatsApp will render.

    Args:
        definition: The flow's JSON definition.
        content_types: Content SID to its ``types`` mapping.

    Returns:
        Descriptions of widgets whose template exceeds a channel limit.

    A list picker takes at most :data:`MAX_LIST_ITEMS` rows. Quick replies take
    at most :data:`MAX_SESSION_BUTTONS` in session; the opener may carry more,
    because it is approved.

    Beyond the API limit there is a research reason to stay well under both:
    every option past the first few is one more scroll on a phone, and options
    people do not scroll to are options nobody picks. If a question needs more
    than ten answers it needs splitting, not a longer list.

    """
    openers = set(opening_sends(definition))
    problems = []

    for state in definition.get("states", []):
        sid = state.get("properties", {}).get("content_sid")
        if not sid or sid not in content_types:
            continue
        name = state.get("name", "")
        types = content_types[sid]
        if not isinstance(types, dict):
            continue

        def _entries(type_name: str, key: str) -> list:
            """Read a list out of one content type, tolerating odd shapes."""
            config = types.get(type_name)
            entries = config.get(key) if isinstance(config, dict) else None
            return entries if isinstance(entries, list) else []

        items = _entries("twilio/list-picker", "items")
        if len(items) > MAX_LIST_ITEMS:
            problems.append(
                f"{name}: list picker has {len(items)} options, "
                f"the maximum is {MAX_LIST_ITEMS}"
            )

        actions = _entries("twilio/quick-reply", "actions")
        if actions and name not in openers and len(actions) > MAX_SESSION_BUTTONS:
            problems.append(
                f"{name}: quick reply has {len(actions)} buttons and is sent in "
                f"session, where WhatsApp permits {MAX_SESSION_BUTTONS}. Use a "
                "list picker instead - it needs no approval either"
            )
    return problems


def check_flow(
    definition: dict, content_types: dict[str, dict[str, Any]] | None = None
) -> list[Finding]:
    """Run the structural checks over a flow definition.

    This is the high-frequency-check equivalent for a Studio flow: it does not
    look at collected data, it verifies that the instrument was *coded*
    correctly, so that the data it produces will be analysable. Run it before a
    round starts and after any edit.

    Args:
        definition: The flow's JSON definition.
        content_types: Optional map of content SID to the content types it
            declares, as returned by the Content API. Supplying it enables the
            checks that depend on what a template actually is rather than on
            the shape of the flow; without it those checks are skipped rather
            than guessed at.

    Returns:
        Findings, errors first. An empty list means every check passed.

    """
    states = definition.get("states", [])
    by_name = {s.get("name"): s for s in states}
    findings: list[Finding] = []
    publishes = any(_is_publish_widget(s) for s in states)

    # Every break-off must still produce a row.
    stranded = unpublished_paths(definition)
    if stranded:
        findings.append(
            Finding(
                "error",
                "unpublished-paths",
                f"{len(stranded)} break-off path(s) never reach the publish widget",
                [f"{w} --{e}--> {d}" for w, e, d in stranded[:10]],
            )
        )

    # Every question must handle non-response and delivery failure.
    missing_timeout, missing_failure = [], []
    for state in states:
        if state.get("type") not in QUESTION_TYPES:
            continue
        events = {t.get("event") for t in state.get("transitions", [])}
        if "timeout" not in events:
            missing_timeout.append(state["name"])
        if "deliveryFailure" not in events:
            missing_failure.append(state["name"])

    if missing_timeout:
        findings.append(
            Finding(
                "error",
                "unhandled-timeout",
                f"{len(missing_timeout)} question(s) do not handle a timeout",
                missing_timeout[:10],
            )
        )
    if missing_failure:
        findings.append(
            Finding(
                "error",
                "unhandled-delivery-failure",
                f"{len(missing_failure)} question(s) do not handle delivery failure",
                missing_failure[:10],
            )
        )

    # A published row must say how the survey ended.
    if publishes:
        columns = [key for key, _ in published_columns(definition)]
        if not any(c.startswith(_FINAL_STATUS_PREFIXES) for c in columns):
            findings.append(
                Finding(
                    "error",
                    "no-final-status",
                    "Published payload carries no final-status variable "
                    "(set_complete / set_no_reply / set_fail / set_multierror)",
                )
            )

    # A flow launched from `rtt launch` is started over the REST API, which
    # fires incomingRequest. If the trigger does not route it, the execution
    # ends immediately having sent nothing - and the launcher still records it
    # as `active`, so the run looks successful while every respondent gets
    # silence.
    trigger = next((s for s in states if s.get("type") == "trigger"), None)
    if trigger is not None:
        routed = {
            t.get("event") for t in trigger.get("transitions", []) if t.get("next")
        }
        if "incomingRequest" not in routed:
            findings.append(
                Finding(
                    "error",
                    "trigger-ignores-api-launch",
                    "Trigger does not route incomingRequest, so executions "
                    "created by `rtt launch` end immediately without sending",
                )
            )

    # The message that opens a business-initiated conversation must be an
    # approved template. This fails at the very first message, for everyone in
    # the round at once, so it is worth catching before the round and not from
    # the error logs afterwards.
    plain_opening, cannot_open = [], []
    for name in opening_sends(definition):
        properties = by_name.get(name, {}).get("properties", {})
        sid = properties.get("content_sid")
        if not sid:
            plain_opening.append(name)
            continue
        blocked = sorted(set((content_types or {}).get(sid, {})) & _CANNOT_OPEN_SESSION)
        if blocked:
            cannot_open.append(f"{name} ({sid}: {', '.join(blocked)})")

    if plain_opening:
        findings.append(
            Finding(
                "error",
                "opening-not-a-template",
                f"{len(plain_opening)} opening message(s) send a free-form "
                "body, which WhatsApp rejects with 63016 outside the 24-hour "
                "window",
                plain_opening[:10],
            )
        )
    if cannot_open:
        findings.append(
            Finding(
                "error",
                "opening-cannot-open-session",
                f"{len(cannot_open)} opening message(s) use a content type "
                "that cannot start a conversation",
                cannot_open[:10],
            )
        )

    oversized = oversized_option_sets(definition, content_types or {})
    if oversized:
        findings.append(
            Finding(
                "error",
                "too-many-options",
                f"{len(oversized)} interactive message(s) exceed what WhatsApp "
                "will render",
                oversized[:10],
            )
        )

    long_errors, long_warnings = overlong_content_text(definition, content_types or {})
    if long_errors:
        findings.append(
            Finding(
                "error",
                "text-too-long",
                f"{len(long_errors)} interactive string(s) exceed the limit "
                "WhatsApp will render",
                long_errors[:10],
            )
        )
    if long_warnings:
        findings.append(
            Finding(
                "warning",
                "text-may-truncate",
                f"{len(long_warnings)} string(s) are near an undocumented limit",
                long_warnings[:10],
            )
        )

    # A condition that cannot match anything routes every respondent down the
    # noMatch branch while looking correct on the canvas.
    broken = unmatchable_conditions(definition)
    if broken:
        findings.append(
            Finding(
                "error",
                "unmatchable-condition",
                f"{len(broken)} split condition(s) can never match",
                broken[:10],
            )
        )

    # Splits that cannot handle an unexpected answer strand the respondent.
    no_fallback = [
        s["name"]
        for s in states
        if s.get("type") == "split-based-on"
        and not any(t.get("event") == "noMatch" for t in s.get("transitions", []))
    ]
    if no_fallback:
        findings.append(
            Finding(
                "warning",
                "split-without-nomatch",
                f"{len(no_fallback)} split(s) have no noMatch branch",
                no_fallback[:10],
            )
        )

    # PII protection.
    if publishes and not summarize(definition)["encrypting"]:
        findings.append(
            Finding(
                "warning",
                "no-encryption",
                "Publishes to Sheets with no encryption widget; any identifier "
                "in the payload is written in plain text",
            )
        )

    # Ambiguous blanks.
    if publishes:
        unpaired = unpaired_answers(definition)
        if unpaired:
            findings.append(
                Finding(
                    "warning",
                    "unpaired-answers",
                    f"{len(unpaired)} answer(s) publish with no status beside "
                    "them, so a blank cannot be read as timed-out vs not-asked",
                    unpaired[:10],
                )
            )

    secrets = scan_for_secrets(definition)
    if secrets:
        findings.append(
            Finding("error", "credentials", "Definition contains secrets", secrets)
        )

    return sorted(findings, key=lambda f: (f.severity != "error", f.code))


def validate_remote(client: Client, name: str, definition: dict) -> list[str]:
    """Ask Twilio whether a definition is structurally valid.

    Args:
        client: An authenticated Twilio client, used for its credentials.
        name: Friendly name to validate under.
        definition: The flow's JSON definition.

    Returns:
        Error messages from Twilio, empty if the definition is valid.

    """
    import requests
    from requests.auth import HTTPBasicAuth

    response = requests.post(
        "https://studio.twilio.com/v2/Flows/Validate",
        auth=HTTPBasicAuth(client.username, client.password),
        data={
            "FriendlyName": name,
            "Status": "draft",
            "Definition": json.dumps(definition),
        },
        timeout=30,
    )
    body = response.json()
    if body.get("valid"):
        return []

    details = body.get("details") or {}
    errors = details.get("errors") or [{"message": body.get("message", "invalid")}]
    return [
        f"{e.get('property_path', '')} {e.get('message', '')}".strip() for e in errors
    ]


#: Matches the flow SID out of a Studio webhook URL, which is the shape Twilio
#: writes into a phone number's sms_url when you wire it to a flow in the console.
_FLOW_WEBHOOK_PATTERN = re.compile(r"/Flows/(FW[0-9a-fA-F]{32})")


#: Where WhatsApp senders live. They are NOT the same resource as the phone
#: number that shares their digits.
_SENDERS_URL = "https://messaging.twilio.com/v2/Channels/Senders"


def _whatsapp_sender_flow(client: Client, address: str) -> str | None:
    """Return the flow on a WhatsApp sender's inbound webhook."""
    try:
        response = client.request(
            "GET", _SENDERS_URL, params={"Channel": "whatsapp", "PageSize": 100}
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a FlowError below
        raise FlowError(f"Could not list WhatsApp senders: {exc}") from exc

    if response.status_code >= 300:
        raise FlowError(f"Could not list WhatsApp senders: HTTP {response.status_code}")

    for sender in json.loads(response.text).get("senders", []):
        if (sender.get("sender_id") or "") != address:
            continue
        webhook = sender.get("webhook") or {}
        match = _FLOW_WEBHOOK_PATTERN.search(webhook.get("callback_url") or "")
        return match.group(1) if match else None
    return None


def inbound_flow_sid(client: Client, from_number: str) -> str | None:
    """Return the flow that owns the inbound webhook for a sending address.

    Args:
        client: An authenticated Twilio client.
        from_number: The sending address. A ``whatsapp:`` prefix selects the
            WhatsApp sender rather than the phone number of the same digits.

    Returns:
        The flow SID handling replies, or None if the address is not found or
        its webhook does not point at a Studio flow (a Messaging Service or a
        custom URL, for instance).

    This is the routing rule that is easy to miss and expensive to miss: a
    Studio execution only receives a reply if the inbound webhook on the address
    it sent from points at **that flow**. Send from an address wired to a
    different flow and every message goes out perfectly, every respondent
    replies, and the other flow answers them - while your executions sit
    untouched until they time out. The send side looks completely healthy and
    the round collects nothing.

    **A WhatsApp sender is a different resource from the phone number whose
    digits it shares, and carries its own webhook.** The number's ``sms_url``
    governs SMS only. Checking the number for a WhatsApp round reports whatever
    SMS is wired to and is simply the wrong answer - which is worse than no
    check, because it is a green light. This function follows the prefix.

    On a shared account, one address routes to exactly one flow, so whoever
    launched last owns it. Expect to have to repoint it every round.

    """
    address = from_number.strip()
    if not address:
        return None

    if address.startswith("whatsapp:"):
        return _whatsapp_sender_flow(client, address)

    try:
        numbers = client.incoming_phone_numbers.list(limit=200)
    except TwilioRestException as exc:
        raise FlowError(
            f"Could not list phone numbers: HTTP {exc.status} "
            f"(code {exc.code}): {exc.msg}"
        ) from exc

    for number in numbers:
        if number.phone_number != address:
            continue
        match = _FLOW_WEBHOOK_PATTERN.search(number.sms_url or "")
        return match.group(1) if match else None
    return None


def published_revision(client: Client, flow_id: str) -> int | None:
    """Return the flow's latest published revision, or None if never published.

    Args:
        client: An authenticated Twilio client.
        flow_id: A flow SID starting ``FW``.

    Returns:
        The revision number, or None if the flow has no published revision.

    Executions run the latest *published* revision, not the latest draft. A flow
    that has never been published therefore cannot run at all - which is worth
    knowing before a launch rather than after every row in the round has failed.

    """
    try:
        revision = client.studio.v2.flows(flow_id).revisions("LatestPublished").fetch()
    except TwilioRestException as exc:
        if exc.status == 404:
            return None
        raise FlowError(
            f"Could not read published revision of {flow_id}: "
            f"HTTP {exc.status} (code {exc.code}): {exc.msg}"
        ) from exc
    return revision.revision


def referenced_content_types(
    client: Client, definition: dict
) -> dict[str, dict[str, Any]]:
    """Look up what every content template the flow references actually is.

    Args:
        client: An authenticated Twilio client.
        definition: The flow's JSON definition.

    Returns:
        Content SID to its ``types`` mapping, as the Content API returns it -
        type name to that type's configuration, so the options inside a list or
        the buttons on a quick reply can be counted. SIDs that cannot be
        fetched are omitted, so the checks that use this skip them rather than
        treating an unreachable template as a broken one.

    """
    sids = {
        state.get("properties", {}).get("content_sid")
        for state in definition.get("states", [])
    }
    types: dict[str, dict[str, Any]] = {}
    for sid in sorted(s for s in sids if s):
        try:
            types[sid] = dict(client.content.v1.contents(sid).fetch().types or {})
        except Exception:  # noqa: BLE001 - a missing template is not our error
            logger.warning("Could not fetch content template %s", sid)
    return types


def deploy(
    *,
    client: Client,
    name: str,
    definition: dict,
    publish: bool = False,
    force: bool = False,
) -> tuple[str, list[Finding]]:
    """Create or update a Studio flow, refusing to ship a broken one.

    Args:
        client: An authenticated Twilio client.
        name: The flow's friendly name. An existing flow with this name is
            updated rather than duplicated.
        definition: The flow's JSON definition.
        publish: Publish rather than leaving it a draft.
        force: Deploy despite check errors. Warnings never block.

    Returns:
        The flow SID and the findings that were reported.

    Raises:
        FlowError: If the checks find errors and ``force`` is not set, if
            Twilio rejects the definition, or if the API call fails.

    The gate exists because this class of defect spreads by duplication. Seven
    flows on this account share one identical break-off path that never reaches
    the publish widget - the same bug copied six times when flows were cloned
    from each other, six of them published. Detection that has to be remembered
    does not stop that; a deploy that refuses does.

    """
    import requests
    from requests.auth import HTTPBasicAuth

    findings = check_flow(definition, referenced_content_types(client, definition))
    errors = [f for f in findings if f.severity == "error"]
    if errors and not force:
        detail = "\n".join(f"  [{f.code}] {f.summary}" for f in errors)
        raise FlowError(
            f"Refusing to deploy {name!r}: {len(errors)} check error(s).\n{detail}\n\n"
            "These are the defects that produce unusable data - break-offs that "
            "publish no row, questions that strand non-responders. Fix them, or "
            "pass --force if you accept the consequence."
        )

    problems = validate_remote(client, name, definition)
    if problems:
        raise FlowError(
            "Twilio rejected the definition:\n" + "\n".join(f"  {p}" for p in problems)
        )

    auth = HTTPBasicAuth(client.username, client.password)
    status = "published" if publish else "draft"

    existing = next((f for f in list_flows(client) if f.friendly_name == name), None)
    payload = {"Status": status, "Definition": json.dumps(definition)}

    if existing is not None:
        url = f"https://studio.twilio.com/v2/Flows/{existing.sid}"
    else:
        url = "https://studio.twilio.com/v2/Flows"
        payload["FriendlyName"] = name

    response = requests.post(url, auth=auth, data=payload, timeout=60)
    if response.status_code >= 300:
        raise FlowError(
            f"Deploy failed: HTTP {response.status_code} {response.text[:300]}"
        )

    body = response.json()
    logger.info(
        "%s %r as %s (revision %s)",
        "Updated" if existing else "Created",
        name,
        status,
        body.get("revision"),
    )
    return body["sid"], findings


def preloaded_keys(definition: dict) -> set[str]:
    """Return the ``flow.data.*`` values a flow expects to be preloaded.

    Args:
        definition: The flow's JSON definition.

    Returns:
        Every key referenced as ``{{flow.data.<key>}}`` anywhere in the flow -
        message bodies, split conditions, function parameters.

    These are the Studio equivalent of SurveyCTO preloads: they come from the
    sample file at launch, and a key the flow references but the launcher does
    not send resolves to an empty string. Nothing errors, the messages simply
    go out with a blank where the respondent's name should be, and the publish
    widget writes empty columns. That failure is only visible after the round.

    """
    return set(_PRELOAD_PATTERN.findall(json.dumps(definition, ensure_ascii=False)))


def check_preloaded(definition: dict, sending: set[str]) -> tuple[set[str], set[str]]:
    """Compare what a flow expects against what the launcher will send.

    Args:
        definition: The flow's JSON definition.
        sending: Column names that will be passed as execution parameters.

    Returns:
        A tuple of (missing, unused):

        * ``missing`` - referenced by the flow but not being sent. These become
          empty strings in the survey and blank columns in the output.
        * ``unused`` - being sent but never referenced. Usually harmless, but a
          near-match to a missing key is the signature of a typo.

    """
    expected = preloaded_keys(definition)
    return expected - sending, sending - expected


def pull(
    *,
    client: Client,
    identifier: str,
    destination: Path,
    allow_secrets: bool = False,
) -> Path:
    """Fetch a flow definition and write it as formatted JSON.

    Args:
        client: An authenticated Twilio client.
        identifier: Flow SID or friendly name.
        destination: Directory to write into.
        allow_secrets: Write even if the scan finds something. Off by default:
            a flow definition is meant to be committed, and this repository has
            published a live credential before.

    Returns:
        The path written.

    Raises:
        FlowError: If the flow cannot be fetched, or the scan finds something
            and ``allow_secrets`` is not set.

    """
    flow = resolve_flow(client, identifier)
    definition = flow.definition

    if not definition:
        raise FlowError(
            f"Flow {flow.friendly_name!r} ({flow.sid}) returned an empty definition. "
            "Writing it would produce a file that looks like a flow but contains "
            "nothing."
        )

    findings = scan_for_secrets(definition)
    if findings:
        message = "\n".join(f"  - {f}" for f in findings)
        if not allow_secrets:
            raise FlowError(
                f"Flow {flow.friendly_name!r} contains credential-shaped strings:\n"
                f"{message}\n\n"
                "Refusing to write it, because flow definitions are meant to be "
                "committed. Move the value into a Function environment variable, "
                "or re-run with --allow-secrets if these are false positives."
            )
        logger.warning(
            "Writing %r despite credential-shaped strings:\n%s",
            flow.friendly_name,
            message,
        )

    destination.mkdir(parents=True, exist_ok=True)
    # Name by friendly name for readability, keeping the SID for exactness.
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", flow.friendly_name).strip("_")
    path = destination / f"{safe}.json"

    payload = {
        "friendly_name": flow.friendly_name,
        "sid": flow.sid,
        "status": flow.status,
        "revision": flow.revision,
        "date_updated": flow.date_updated.isoformat() if flow.date_updated else None,
        "definition": definition,
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    logger.info("Pulled %r (rev %s) to %s", flow.friendly_name, flow.revision, path)
    return path
