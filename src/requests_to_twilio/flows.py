"""Pull Twilio Studio flow definitions into the repository.

A Studio flow *is* the survey instrument: its questions, their wording, the
branching between them. Built in the web editor it lives only in Twilio, where
nobody can diff it, review it, or say what a respondent was actually asked six
months ago. Flow revision numbers in the hundreds or thousands are normal, and
none of those edits are recorded anywhere a researcher can see.

The definition is plain JSON over the API, so it can simply be checked in.

Definitions are scanned for credentials before they are written. A flow can
legitimately reference service SIDs, but a Function widget's parameters are
free-form and people do paste keys into them - and this repository has already
published one live private key.
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


def check_flow(definition: dict) -> list[Finding]:
    """Run the structural checks over a flow definition.

    This is the high-frequency-check equivalent for a Studio flow: it does not
    look at collected data, it verifies that the instrument was *coded*
    correctly, so that the data it produces will be analysable. Run it before a
    round starts and after any edit.

    Args:
        definition: The flow's JSON definition.

    Returns:
        Findings, errors first. An empty list means every check passed.

    """
    states = definition.get("states", [])
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
