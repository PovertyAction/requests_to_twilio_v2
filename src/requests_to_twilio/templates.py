"""Create WhatsApp content templates and submit them to Meta for approval.

Any message a survey sends *before* the respondent has replied is
business-initiated, and WhatsApp requires those to come from an approved
template. Since 1 April 2025 sending template text in ``Body`` fails outright
with error 63016; templates must be sent by ``ContentSid``. So the opt-in
message of every flow has to exist here first.

Template definitions live as JSON under ``templates/`` so the exact wording sent
to respondents is version-controlled and reviewable. That matters more than
usual here for one reason:

    **A submitted template can never be edited.**

Meta has no update operation. Changing a word means creating a new template,
under a new name, and waiting for approval again. Reviewing a diff beforehand is
the only chance to get it right.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
from twilio.rest.content.v1.content import ContentList
from twilio.rest.content.v1.content.approval_create import ApprovalCreateList

from .log import get_logger

logger = get_logger()

#: Categories Meta recognises. UTILITY covers transactional messages tied to
#: something the recipient has already signed up for, which is what a research
#: survey invitation is. MARKETING is subject to per-user limits and easier
#: opt-outs, so it delivers less reliably. Meta assigns the final category and
#: can disagree with whatever is requested here.
CATEGORIES = ("UTILITY", "MARKETING", "AUTHENTICATION")

#: Keys in a definition file that are documentation, not payload.
_COMMENT_KEYS = ("_comment", "_comments", "_note")


class TemplateError(Exception):
    """Raised when a template cannot be created, submitted, or read."""


class _RawTypes:
    """Passes a plain dict through the SDK's ``types`` field.

    ``ContentCreateRequest.to_dict()`` calls ``.to_dict()`` on whatever it is
    given, so a bare dict raises AttributeError. This wrapper satisfies that
    without forcing callers to build the SDK's nested type objects by hand.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        """Return the wrapped dict unchanged."""
        return self._data


def load_definition(path: Path) -> dict[str, Any]:
    """Read and validate a template definition file.

    Args:
        path: A JSON file under ``templates/``.

    Returns:
        The definition, with documentation keys stripped.

    Raises:
        TemplateError: If the file is unreadable or missing required fields.

    """
    if not path.is_file():
        raise TemplateError(f"Template definition not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"{path} is not valid JSON: {exc}") from exc

    definition = {k: v for k, v in raw.items() if k not in _COMMENT_KEYS}

    for field in ("friendly_name", "language", "types"):
        if not definition.get(field):
            raise TemplateError(f"{path} is missing required field {field!r}")

    if not isinstance(definition["types"], dict) or not definition["types"]:
        raise TemplateError(f"{path}: 'types' must be a non-empty object")

    return definition


def check_variables(definition: dict[str, Any]) -> list[str]:
    """Warn about wording Meta commonly rejects.

    Args:
        definition: A loaded template definition.

    Returns:
        Human-readable warnings, empty if nothing looks risky. These are
        advisory: Meta is the only authority on what it will approve, and a
        rejection costs a whole review cycle.

    """
    warnings: list[str] = []
    declared = definition.get("variables") or {}

    for type_name, body in definition["types"].items():
        text = (body.get("body") or body.get("title") or "").strip()
        if not text:
            continue

        placeholders = {p for p in _placeholders(text)}
        missing = placeholders - set(declared)
        if missing:
            warnings.append(
                f"{type_name}: {{{{{'}}, {{'.join(sorted(missing))}}}}} used but no "
                "sample given in 'variables'. Meta rejects templates whose "
                "variables have no sample."
            )

        if text.startswith("{{") or text.endswith("}}"):
            warnings.append(
                f"{type_name}: body starts or ends with a variable. Meta requires "
                "a sample for these and often rejects them."
            )

        for action in body.get("actions") or []:
            title = action.get("title") or ""
            if len(title) > 20:
                warnings.append(
                    f"{type_name}: button title {title!r} is {len(title)} characters; "
                    "WhatsApp truncates at 20."
                )

    return warnings


def _placeholders(text: str) -> list[str]:
    """Extract ``{{1}}``-style placeholder names from template text."""
    import re

    return re.findall(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", text)


def find_by_name(client: Client, name: str) -> Any | None:
    """Return the content resource with this friendly name, or None."""
    for content in client.content.v1.contents.list(limit=1000):
        if content.friendly_name == name:
            return content
    return None


def create(client: Client, definition: dict[str, Any]) -> Any:
    """Create a content template in Twilio.

    This does not submit it to Meta; the template exists only in Twilio until
    :func:`submit` is called, so it can still be deleted and redone at this
    stage.

    Args:
        client: An authenticated Twilio client.
        definition: A loaded definition.

    Returns:
        The created content resource.

    Raises:
        TemplateError: If Twilio rejects the request.

    """
    payload = dict(definition)
    payload["types"] = _RawTypes(definition["types"])

    request = ContentList.ContentCreateRequest(payload)

    try:
        content = client.content.v1.contents.create(content_create_request=request)
    except TwilioRestException as exc:
        raise TemplateError(
            f"Could not create {definition['friendly_name']!r}: "
            f"HTTP {exc.status} (code {exc.code}): {exc.msg}"
        ) from exc

    logger.info("Created %s (%s)", content.friendly_name, content.sid)
    return content


def submit(client: Client, sid: str, name: str, category: str) -> Any:
    """Submit a template to Meta for WhatsApp approval.

    Args:
        client: An authenticated Twilio client.
        sid: The content SID, starting ``HX``.
        name: The template name Meta will see. Lowercase, underscores only.
        category: One of :data:`CATEGORIES`.

    Returns:
        The approval request resource.

    Raises:
        TemplateError: On a bad category, or if Twilio rejects the submission.

    This step is irreversible. Once submitted, the wording is frozen: Meta has
    no edit operation, so a change means a new template and a new review.

    """
    if category not in CATEGORIES:
        raise TemplateError(
            f"category must be one of {', '.join(CATEGORIES)}, got {category!r}"
        )

    request = ApprovalCreateList.ContentApprovalRequest(
        {"name": name, "category": category}
    )

    try:
        result = client.content.v1.contents(sid).approval_create.create(
            content_approval_request=request
        )
    except TwilioRestException as exc:
        raise TemplateError(
            f"Could not submit {sid}: HTTP {exc.status} (code {exc.code}): {exc.msg}"
        ) from exc

    logger.info("Submitted %s to Meta as %r (%s)", sid, name, category)
    return result


def approval_status(client: Client, sid: str) -> dict[str, Any]:
    """Fetch a template's WhatsApp approval status.

    Args:
        client: An authenticated Twilio client.
        sid: The content SID.

    Returns:
        A mapping with at least ``status``; also ``category`` and
        ``rejection_reason`` when Meta supplies them.

    Raises:
        TemplateError: If the status cannot be read.

    """
    try:
        approval = client.content.v1.contents(sid).approval_fetch().fetch()
    except TwilioRestException as exc:
        raise TemplateError(
            f"Could not read approval for {sid}: "
            f"HTTP {exc.status} (code {exc.code}): {exc.msg}"
        ) from exc

    whatsapp = getattr(approval, "whatsapp", None) or {}
    return {
        "status": whatsapp.get("status", "unknown"),
        "category": whatsapp.get("category"),
        "rejection_reason": whatsapp.get("rejection_reason"),
        "name": whatsapp.get("name"),
    }


def list_templates(
    client: Client, name_filter: str | None = None
) -> list[dict[str, Any]]:
    """List content templates with their approval status.

    Args:
        client: An authenticated Twilio client.
        name_filter: Case-insensitive substring to match against friendly names.

    Returns:
        One dict per template, newest first.

    """
    try:
        items = client.content.v1.content_and_approvals.list(limit=1000)
    except TwilioRestException as exc:
        raise TemplateError(
            f"Could not list templates: HTTP {exc.status} (code {exc.code}): {exc.msg}"
        ) from exc

    rows = []
    for item in items:
        if name_filter and name_filter.lower() not in item.friendly_name.lower():
            continue
        approvals = item.approval_requests or {}
        rows.append(
            {
                "sid": item.sid,
                "friendly_name": item.friendly_name,
                "language": item.language,
                "status": approvals.get("status", "unsubmitted"),
                "category": approvals.get("category"),
                "types": list((item.types or {}).keys()),
            }
        )
    return rows
