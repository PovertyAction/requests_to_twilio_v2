"""Studio widget builders, shared by the survey compiler and the demo builder.

Each function returns one state of a flow definition as plain data. They are
deliberately thin: what a widget *means* in a survey - which reply counts, what
gets stored, where a timeout goes - is decided by the caller, and these only make
sure every widget is wired the way Studio needs (both send events routed, all
three wait events handled, a noMatch on every split).

These lived in ``scripts/build_data_use_demo.py`` while the demo was the only
flow anybody generated. The compiler needs the same widgets, and a script is
not importable.
"""

from __future__ import annotations

from typing import Any

FROM = "{{flow.channel.address}}"
TIMEOUT = "3600"  # 1 hour, as in the source flow

#: The Functions service `just setup functions` creates. Everything about the
#: deployment is looked up from this one name at build time rather than pasted
#: in as SIDs: a `ZS`/`ZE`/`ZH` triple and a `*.twil.io` host are per-account,
#: and the domain carries a random suffix that cannot even be guessed. Hard-
#: coding them meant `just demo build` emitted a flow pointing at the
#: author's own subaccount, which 404s for everybody else - the single thing
#: that stopped another team running this.
FUNCTIONS_SERVICE_NAME = "rtt-survey"

#: The publish parameter naming the MotherDuck table a flow writes to. Not a
#: column: publish_motherduck.js reserves it, and the schema tools skip it.
TABLE_PARAMETER = "rtt_table"
ENCRYPT_FUNCTION_NAME = "encrypt_fields"

#: The path the encryption function is deployed at, from
#: `deploy_twilio_functions.py`.
ENCRYPT_PATH = "/encrypt-fields"

#: Where a submission is written, and therefore which Function the last widget
#: calls. Both destinations are real and both are supported; they differ in what
#: the person running the round has to obtain first.
#:
#:   motherduck  an INSERT over the Postgres wire protocol. No column ceiling,
#:               no per-write quota, and the data lands where analysis already
#:               happens. Needs a MotherDuck account and a token.
#:   gsheets     an append to a spreadsheet's next row. The lowest barrier there
#:               is - a sheet, a service account, and anybody on the team can
#:               open the result and read it - which is why it stays a
#:               first-class path rather than a legacy one. Needs a Google Cloud
#:               service account, and carries the Sheets API quota and the
#:               172-column ceiling of a header-row lookup.
#:
#: The widget name is part of the target because it is what a reviewer reads on
#: the Studio canvas, and a canvas that says `publish_motherduck` while writing
#: to a spreadsheet is worse than no label at all. Every name here starts with
#: `publish_`, which is what `rtt flow check` matches on to find the step that
#: writes the row - see `_is_publish_widget` in flows.py.
PUBLISH_TARGETS: dict[str, dict[str, str]] = {
    "motherduck": {
        "function": "publish_motherduck",
        "path": "/publish-motherduck",
        "widget": "publish_motherduck",
    },
    "gsheets": {
        "function": "publish_gsheets",
        "path": "/publish-gsheets",
        "widget": "publish_gsheets",
    },
}

#: What a build writes when told nothing.
#:
#: Google Sheets, because this is the choice a new user should not have to make
#: before their first round. A spreadsheet and a service account is the shortest
#: path from "cloned the repo" to "watched a reply arrive", and everyone on a
#: team can open the result without being taught a query language. MotherDuck is
#: better for a long instrument or for analysis that outlives collection, and it
#: is one flag away.
#:
#: Neither is the right answer for everybody - this is a per-round decision, and
#: `--publish-target` is how it is made. What a default settles is only which
#: one you get by not deciding.
DEFAULT_PUBLISH_TARGET = "gsheets"


class BuildError(Exception):
    """Raised when a flow cannot be built: bad input, or a missing dependency."""


def send(name, body, next_state, *, x=0, y=0):
    """Build a one-way message widget."""
    return {
        "name": name,
        "type": "send-message",
        "properties": {"offset": {"x": x, "y": y}, "from": FROM, "body": body},
        "transitions": [
            {"event": "sent", "next": next_state},
            {"event": "failed", "next": next_state},
        ],
    }


def send_content(name, content_sid, *, x=0, y=0, variables=None):
    """Build a terminal one-way message that sends an approved template.

    Used for the closing message to somebody who never replied. They never
    opened the 24-hour window, so this send is business-initiated and a
    free-form body would fail with 63016 - a template is the only way to reach
    them at all.
    """
    properties = {
        "offset": {"x": x, "y": y},
        "from": FROM,
        "message_type": "content_template",
        "content_sid": content_sid,
    }
    if variables:
        properties["content_variables"] = variables
    return {
        "name": name,
        "type": "send-message",
        "properties": properties,
        "transitions": [{"event": "sent"}, {"event": "failed"}],
    }


def ask(
    name,
    body,
    on_reply,
    *,
    x=0,
    y=0,
    on_timeout="mark_no_reply",
    timeout=TIMEOUT,
    on_delivery_failure="mark_delivery_failed",
):
    """Build a free-text question. Wires reply, timeout and deliveryFailure."""
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": {
            "offset": {"x": x, "y": y},
            "from": FROM,
            "body": body,
            "timeout": timeout,
        },
        "transitions": [
            {"event": "incomingMessage", "next": on_reply},
            {"event": "timeout", "next": on_timeout},
            {"event": "deliveryFailure", "next": on_delivery_failure},
        ],
    }


def ask_content(
    name,
    content_sid,
    on_reply,
    *,
    x=0,
    y=0,
    variables=None,
    on_timeout="mark_no_reply",
    timeout=TIMEOUT,
    on_delivery_failure="mark_delivery_failed",
):
    """Build a question that sends a content template instead of a body.

    Used for the interactive messages - buttons and lists. Studio needs
    `message_type` set as well as the SID, otherwise it treats the widget as a
    plain body and sends nothing.
    """
    properties = {
        "offset": {"x": x, "y": y},
        "from": FROM,
        "message_type": "content_template",
        "content_sid": content_sid,
        "timeout": timeout,
    }
    if variables:
        properties["content_variables"] = variables
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": properties,
        "transitions": [
            {"event": "incomingMessage", "next": on_reply},
            {"event": "timeout", "next": on_timeout},
            {"event": "deliveryFailure", "next": on_delivery_failure},
        ],
    }


def set_vars(name, pairs, next_state, *, x=0, y=0):
    """Set flow variables."""
    return {
        "name": name,
        "type": "set-variables",
        "properties": {
            "offset": {"x": x, "y": y},
            "variables": [{"key": k, "value": v} for k, v in pairs],
        },
        "transitions": [{"event": "next", "next": next_state}],
    }


def split(name, tested, branches, default, *, x=0, y=0, condition="equal_to"):
    """Branch on a value. `default` is the mandatory noMatch destination.

    `branches` is a list of (value, destination) or (value, destination, label),
    or (value, destination, label, condition) when one branch needs a
    different comparison from the rest. The label is what the Studio console
    shows on the transition, so it is worth setting whenever the raw value is a
    long list: someone will read this canvas mid-session.
    """
    transitions = [{"event": "noMatch", "next": default}]
    for branch in branches:
        value, destination = branch[0], branch[1]
        label = branch[2] if len(branch) > 2 else f"is {value}"
        kind = branch[3] if len(branch) > 3 else condition
        transitions.append(
            {
                "event": "match",
                "next": destination,
                "conditions": [
                    {
                        "friendly_name": label[:60],
                        "arguments": [tested],
                        "type": kind,
                        "value": value,
                    }
                ],
            }
        )
    return {
        "name": name,
        "type": "split-based-on",
        "properties": {"offset": {"x": x, "y": y}, "input": tested},
        "transitions": transitions,
    }


def resolve_functions(client, target: str = DEFAULT_PUBLISH_TARGET) -> dict[str, str]:
    """Look up the deployed Functions service, environment, and both functions.

    Args:
        client: An authenticated Twilio client.
        target: Which publish destination to wire up, a key of
            :data:`PUBLISH_TARGETS`.

    Returns:
        The keys a ``run-function`` widget needs: ``service_sid``,
        ``environment_sid``, ``encrypt_sid``, ``publish_sid``, ``encrypt_url``
        and ``publish_url``, plus ``publish_widget`` - the name the publish step
        carries on the canvas.

    Raises:
        BuildError: If the service, its environment, or either function is
            missing - which means `just setup functions` has not been run on
            this account.

    Studio needs the deployed **url** as well as the three SIDs; validation
    rejects a run-function widget without it, and the domain carries a random
    per-account suffix, so it has to be read from the environment rather than
    constructed.

    """
    if target not in PUBLISH_TARGETS:
        raise BuildError(
            f"Unknown publish target {target!r}. Choose one of "
            f"{', '.join(sorted(PUBLISH_TARGETS))}."
        )
    publish = PUBLISH_TARGETS[target]

    # No `limit=`: the SDK turns it into a page_size, Serverless caps that at
    # 100, and asking for more is a 400 rather than a truncation. Bare .list()
    # walks the pages, so an account with many services still resolves.
    service = next(
        (
            s
            for s in client.serverless.v1.services.list()
            if s.unique_name == FUNCTIONS_SERVICE_NAME
        ),
        None,
    )
    if service is None:
        raise BuildError(
            f"No Functions service named {FUNCTIONS_SERVICE_NAME!r} on this "
            f"account. Run `just setup functions` first - it creates the "
            f"service and deploys encrypt_fields and every publish target."
        )

    environments = client.serverless.v1.services(service.sid).environments.list()
    environment = next(iter(environments), None)
    if environment is None:
        raise BuildError(
            f"The {FUNCTIONS_SERVICE_NAME!r} service has no environment. "
            f"Re-run `just setup functions`."
        )

    functions = {
        f.friendly_name: f.sid
        for f in client.serverless.v1.services(service.sid).functions.list()
    }
    missing = [
        name
        for name in (ENCRYPT_FUNCTION_NAME, publish["function"])
        if name not in functions
    ]
    if missing:
        # Naming the target matters here. A service deployed before this repo
        # grew a second destination has encrypt_fields and publish_motherduck
        # and nothing else, so `--publish-target gsheets` fails on an account
        # that is otherwise perfectly set up - and "re-run just setup functions"
        # alone does not explain why.
        raise BuildError(
            f"Deployed but incomplete: {FUNCTIONS_SERVICE_NAME!r} is missing "
            f"{', '.join(missing)}, which the {target!r} publish target needs. "
            f"Re-run `just setup functions`."
        )

    host = environment.domain_name
    return {
        "service_sid": service.sid,
        "environment_sid": environment.sid,
        "encrypt_sid": functions[ENCRYPT_FUNCTION_NAME],
        "publish_sid": functions[publish["function"]],
        "encrypt_url": f"https://{host}{ENCRYPT_PATH}",
        "publish_url": f"https://{host}{publish['path']}",
        "publish_widget": publish["widget"],
    }


def run_function(
    name, functions, sid_key, url_key, parameters, success, fail, *, x=0, y=0
):
    """Build a run-function widget from :func:`resolve_functions` output."""
    return {
        "name": name,
        "type": "run-function",
        "properties": {
            "offset": {"x": x, "y": y},
            "service_sid": functions["service_sid"],
            "environment_sid": functions["environment_sid"],
            "function_sid": functions[sid_key],
            "url": functions[url_key],
            "parameters": [{"key": k, "value": v} for k, v in parameters],
        },
        "transitions": [
            {"event": "success", "next": success},
            {"event": "fail", "next": fail},
        ],
    }


def placeholder_functions(target: str = DEFAULT_PUBLISH_TARGET) -> dict[str, str]:
    """Return Functions coordinates shaped like real ones, for an offline build.

    An offline build is for reading and checking a flow, never for deploying
    it: the SIDs are zeros and the host does not exist, so Studio rejects it.
    """
    publish = PUBLISH_TARGETS[target]
    host = "rtt-survey-0000-offline.twil.io"
    return {
        "service_sid": "ZS" + "0" * 32,
        "environment_sid": "ZE" + "0" * 32,
        "encrypt_sid": "ZH" + "0" * 31 + "1",
        "publish_sid": "ZH" + "0" * 31 + "2",
        "encrypt_url": f"https://{host}{ENCRYPT_PATH}",
        "publish_url": f"https://{host}{publish['path']}",
        "publish_widget": publish["widget"],
    }


__all__: list[Any] = [
    "BuildError",
    "DEFAULT_PUBLISH_TARGET",
    "ENCRYPT_FUNCTION_NAME",
    "ENCRYPT_PATH",
    "FROM",
    "FUNCTIONS_SERVICE_NAME",
    "PUBLISH_TARGETS",
    "TIMEOUT",
    "ask",
    "ask_content",
    "placeholder_functions",
    "resolve_functions",
    "run_function",
    "send",
    "send_content",
    "set_vars",
    "split",
]
