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
