"""Command-line interface.

Credentials are never accepted as flags. Everything secret comes from the
environment or a ``.env`` file, because arguments on a command line land in
shell history and are readable by any other process on the machine.
"""

from __future__ import annotations

import contextlib
import json
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from twilio.rest import Client

from . import config as cfg
from .crypto import CryptoError, KeyPair, load_private_key
from .decryptor import DecryptionError, decrypt_dataset
from .fetch import FetchError, fetch_executions, reconcile, write_output
from .flows import (
    FlowError,
    check_flow,
    check_preloaded,
    inbound_flow_sid,
    list_flows,
    published_columns,
    published_revision,
    referenced_content_types,
    resolve_flow,
    summarize,
    unpaired_answers,
    unpublished_paths,
)
from .flows import (
    deploy as deploy_flow,
)
from .flows import pull as pull_flow
from .launcher import LaunchError, launch
from .log import configure
from .templates import (
    CATEGORIES,
    TemplateError,
    approval_status,
    check_variables,
    find_by_name,
    list_templates,
    load_definition,
)
from .templates import (
    create as create_template,
)
from .templates import (
    delete as delete_template,
)
from .templates import (
    submit as submit_template,
)
from .warehouse import WarehouseError, push_dataframe, push_file, resolve_database

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Launch Twilio Studio surveys and decrypt the PII they collect.",
)

DEFAULT_PRIVATE_KEY_FILE = Path("rtt_private_key.txt")


def _fail(message: str) -> None:
    """Print an error and exit non-zero."""
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _client() -> tuple[Client, cfg.TwilioConfig]:
    """Build an authenticated Twilio client from the environment."""
    try:
        conf = cfg.TwilioConfig.from_env()
    except cfg.ConfigError as exc:
        _fail(str(exc))
    return Client(conf.account_sid, conf.auth_token), conf


def _check_preloaded_data(
    client: Client, flow_id: str, columns: list[str], *, force: bool
) -> None:
    """Compare a flow's flow.data references against the columns being sent.

    A key the flow references but the launcher does not send resolves to an
    empty string. Nothing errors: messages go out with a blank where the
    respondent's name should be, and the publish widget writes empty columns.
    Since that is only visible after the round, this blocks the send.
    """
    try:
        flow = resolve_flow(client, flow_id)
    except FlowError as exc:
        typer.secho(
            f"  warning: could not check preloaded data ({exc})",
            fg=typer.colors.YELLOW,
        )
        return

    # Executions run the latest *published* revision. Against a flow that has
    # never been published there is nothing to run, so every row in the round
    # fails identically - and the tracker fills with failures that look like a
    # credentials or number problem rather than a missing publish.
    try:
        if published_revision(client, flow.sid) is None:
            typer.secho(
                f"\nFlow {flow.friendly_name!r} has never been published, so it "
                "cannot run.\n"
                "Studio executes the latest published revision, and this flow "
                "has only a draft.\n\n"
                "  just flow-deploy <definition.json> --publish\n",
                fg=typer.colors.RED,
                bold=True,
            )
            raise typer.Exit(code=1)
    except FlowError as exc:
        typer.secho(
            f"  warning: could not check publish state ({exc})", fg=typer.colors.YELLOW
        )

    if not flow.definition:
        return

    # `Number` is always available: the launcher sends it as the destination.
    missing, unused = check_preloaded(flow.definition, set(columns) | {"Number"})

    # ...which also means it is never a preload, so reporting it as an unused
    # one is noise on every single run. A warning that always fires is a
    # warning nobody reads.
    unused = unused - {"Number"}

    if unused:
        typer.secho(
            f"  note: sending column(s) the flow never uses: {', '.join(sorted(unused))}",
            fg=typer.colors.YELLOW,
        )

    if not missing:
        if columns:
            typer.secho(
                f"  preloaded data OK: flow's {len(set(columns))} reference(s) all supplied",
                fg=typer.colors.GREEN,
            )
        return

    typer.secho(
        f"\nFlow {flow.friendly_name!r} references {len(missing)} preloaded "
        f"value(s) you are not sending:\n",
        fg=typer.colors.RED,
        bold=True,
    )
    for key in sorted(missing):
        near = [c for c in columns if c.lower() == key.lower()]
        hint = (
            f"   <- did you mean the column {near[0]!r}? (case differs)" if near else ""
        )
        typer.echo(f"    {{{{flow.data.{key}}}}}{hint}")

    typer.secho(
        "\n  These resolve to empty strings. Messages will go out with blanks, "
        "\n  and the published columns will be empty - visible only after the round."
        "\n  Add them to --columns (they must match the sample file's headers "
        "exactly).",
        fg=typer.colors.YELLOW,
    )

    if force:
        typer.secho("\n  (dry run, continuing)", fg=typer.colors.YELLOW)
        return

    if not typer.confirm("\nSend anyway?"):
        typer.echo("Aborted.")
        raise typer.Exit(code=1)


def _check_inbound_routing(
    client: Client, flow_id: str, from_number: str, *, force: bool
) -> None:
    """Verify replies to this number will actually reach this flow.

    A Studio execution only receives a reply if the sending number's inbound
    webhook points at the same flow. Get this wrong and the send side looks
    perfect - every message delivered, every respondent replying - while a
    different flow answers them and these executions sit untouched until they
    time out. The round collects nothing and nothing reports an error.
    """
    # A bare number is an SMS address to Twilio, and this pipeline is
    # WhatsApp-first, so a missing prefix is usually an oversight rather than a
    # choice. Left unsaid, the check below would read the number's SMS webhook
    # and report on a channel the round may never touch - the same
    # confident-wrong-answer that let a broken round look healthy once already.
    # It is a warning, not a block: an SMS round is a legitimate thing to run.
    if not from_number.startswith("whatsapp:"):
        typer.secho(
            f"  warning: {from_number} has no 'whatsapp:' prefix, so it is an "
            "SMS address.\n"
            "           Inbound routing below describes SMS, not WhatsApp. If "
            "this round is\n"
            "           WhatsApp, set RTT_FROM_NUMBER to "
            f"'whatsapp:{from_number}'.",
            fg=typer.colors.YELLOW,
        )

    try:
        owner = inbound_flow_sid(client, from_number)
    except FlowError as exc:
        typer.secho(
            f"  warning: could not check inbound routing ({exc})",
            fg=typer.colors.YELLOW,
        )
        return

    if owner == flow_id:
        typer.secho(
            "  inbound routing OK: replies to this number reach this flow",
            fg=typer.colors.GREEN,
        )
        return

    if owner is None:
        typer.secho(
            f"  warning: {from_number} has no Studio flow on its inbound "
            "webhook (a Messaging Service or custom URL?). Replies may not "
            "reach this flow - check before sending a real round.",
            fg=typer.colors.YELLOW,
        )
        return

    typer.secho(
        f"\nReplies to {from_number} go to a different flow.\n",
        fg=typer.colors.RED,
        bold=True,
    )
    try:
        other = resolve_flow(client, owner)
        typer.echo(
            f"  that number's inbound webhook -> {other.friendly_name} ({owner})"
        )
    except FlowError:
        typer.echo(f"  that number's inbound webhook -> {owner}")
    typer.echo(f"  you are launching              -> {flow_id}\n")
    typer.secho(
        "  Messages will send fine and respondents will reply, but those\n"
        "  replies reach the other flow. These executions will sit untouched\n"
        "  until they time out, and the round will collect nothing.\n\n"
        "  Repoint the number's inbound webhook at this flow first. Note that\n"
        "  a number can only route to one flow, so this takes it away from\n"
        "  whatever owns it now.",
        fg=typer.colors.YELLOW,
    )

    if force:
        typer.secho("\n  (dry run, continuing)", fg=typer.colors.YELLOW)
        return

    if not typer.confirm("\nSend anyway?"):
        typer.echo("Aborted.")
        raise typer.Exit(code=1)


def _private_key():
    """Load the private key from the environment or the file it points at."""
    inline = cfg.optional("ENCRYPTION_PRIVATE_KEY")
    if inline:
        return load_private_key(inline)

    path_text = cfg.optional("ENCRYPTION_PRIVATE_KEY_FILE")
    path = Path(path_text) if path_text else DEFAULT_PRIVATE_KEY_FILE
    if not path.is_file():
        _fail(
            f"No private key. Set ENCRYPTION_PRIVATE_KEY in .env, or place the "
            f"key file at {path}. Generate a keypair with `just keygen`."
        )
    return load_private_key(path.read_text(encoding="utf-8"))


@app.command()
def keygen(
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Where to write the private key."),
    ] = DEFAULT_PRIVATE_KEY_FILE,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing private key file."),
    ] = False,
) -> None:
    """Generate an X25519 keypair for encrypting survey responses.

    The public key goes into the Twilio Function's environment; the private key
    stays on this machine and is the only thing that can read the data back.
    """
    if out.exists() and not force:
        _fail(
            f"{out} already exists. Overwriting it would permanently destroy "
            f"access to every response encrypted with it. Pass --force only if "
            f"you are certain no collected data depends on that key."
        )

    pair = KeyPair.generate()
    out.write_text(pair.private_b64 + "\n", encoding="utf-8")

    # Best effort on POSIX; a no-op on Windows, where NTFS ACLs govern instead.
    with contextlib.suppress(OSError):
        out.chmod(stat.S_IRUSR | stat.S_IWUSR)

    typer.secho("Keypair generated.\n", fg=typer.colors.GREEN, bold=True)

    typer.secho("PUBLIC KEY", bold=True)
    typer.echo("Paste into the Twilio Function Service as ENCRYPTION_PUBLIC_KEY:\n")
    typer.secho(f"  {pair.public_b64}\n", fg=typer.colors.CYAN)

    typer.secho("PRIVATE KEY", bold=True)
    typer.echo(f"Written to {out}\n")
    typer.secho(
        "  Back this up somewhere safe and access-controlled, exactly as you\n"
        "  would a SurveyCTO private key. If you lose it, every response\n"
        "  encrypted with the matching public key becomes unreadable. It is\n"
        "  already covered by .gitignore - never commit it, never email it.\n",
        fg=typer.colors.YELLOW,
    )


@app.command("launch")
def launch_cmd(
    input_file: Annotated[
        Path, typer.Argument(help="Sample file (.xlsx or .csv) with a Number column.")
    ],
    columns: Annotated[
        str,
        typer.Option("--columns", help="Comma-separated columns to send to the flow."),
    ] = "",
    flow_id: Annotated[
        str | None, typer.Option("--flow-id", help="Studio flow SID. Overrides .env.")
    ] = None,
    from_number: Annotated[
        str | None,
        typer.Option("--from-number", help="Sending number. Overrides .env."),
    ] = None,
    batch_size: Annotated[
        int, typer.Option("--batch-size", help="Messages per batch.")
    ] = 50,
    sec_between_batches: Annotated[
        float, typer.Option("--sleep", help="Seconds to pause between batches.")
    ] = 5.0,
    resume: Annotated[
        bool, typer.Option("--resume", help="Skip numbers already sent successfully.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate without sending anything.")
    ] = False,
    skip_preload_check: Annotated[
        bool,
        typer.Option(
            "--skip-preload-check",
            help="Do not compare the flow's flow.data references against --columns.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Send a Studio flow execution to every number in a sample file."""
    configure(verbose)
    cfg.load_env()
    client, conf = _client()

    try:
        resolved_flow = conf.resolve_flow_id(flow_id)
        resolved_from = conf.resolve_from_number(from_number)
    except cfg.ConfigError as exc:
        _fail(str(exc))

    column_list = [c.strip() for c in columns.split(",") if c.strip()]

    if not skip_preload_check:
        _check_preloaded_data(client, resolved_flow, column_list, force=dry_run)
        _check_inbound_routing(client, resolved_flow, resolved_from, force=dry_run)

    try:
        launch(
            client=client,
            flow_id=resolved_flow,
            from_number=resolved_from,
            input_file=input_file,
            columns_to_send=column_list,
            batch_size=batch_size,
            sec_between_batches=sec_between_batches,
            resume=resume,
            dry_run=dry_run,
        )
    except LaunchError as exc:
        _fail(str(exc))


@app.command("decrypt")
def decrypt_cmd(
    input_file: Annotated[
        Path, typer.Argument(help="Dataset downloaded from Google Sheets.")
    ],
    columns: Annotated[
        str,
        typer.Option(
            "--columns",
            help="Comma-separated columns to decrypt. Auto-detected if omitted.",
        ),
    ] = "",
    legacy_secret: Annotated[
        str | None,
        typer.Option(
            "--legacy-secret",
            help="Pre-2.0 secret, for data collected before this version.",
        ),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Where to write the result.")
    ] = None,
    to_motherduck: Annotated[
        str | None,
        typer.Option(
            "--to-motherduck",
            help="Also load the result into this MotherDuck table.",
        ),
    ] = None,
    database: Annotated[
        str | None, typer.Option("--database", help="MotherDuck database.")
    ] = None,
    warehouse_columns: Annotated[
        str,
        typer.Option(
            "--warehouse-columns",
            help="Restrict the warehouse load to these columns, "
            "e.g. to leave direct identifiers out.",
        ),
    ] = "",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Decrypt the encrypted columns of a collected dataset."""
    configure(verbose)
    cfg.load_env()

    column_list = [c.strip() for c in columns.split(",") if c.strip()]
    secret = legacy_secret or cfg.optional(cfg.ENV_LEGACY_SECRET)

    key = None
    try:
        key = _private_key()
    except CryptoError as exc:
        # Legacy-only files need no keypair, so defer the failure until we know
        # whether any v2 values are actually present.
        if not secret:
            _fail(str(exc))

    try:
        destination, _, _ = decrypt_dataset(
            input_path=input_file,
            private_key=key,
            columns=column_list or None,
            legacy_secret=secret,
            output_path=output,
        )
    except (DecryptionError, CryptoError) as exc:
        _fail(str(exc))

    if to_motherduck:
        subset = [c.strip() for c in warehouse_columns.split(",") if c.strip()]
        try:
            push_file(
                path=destination,
                table=to_motherduck,
                database=resolve_database(database),
                columns=subset or None,
            )
        except WarehouseError as exc:
            _fail(str(exc))


@app.command()
def fetch(
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the executions.")
    ] = Path("executions.csv"),
    flow_id: Annotated[
        str | None, typer.Option("--flow-id", help="Studio flow SID. Overrides .env.")
    ] = None,
    since: Annotated[
        datetime | None,
        typer.Option("--since", formats=["%Y-%m-%d"], help="Only from this date."),
    ] = None,
    until: Annotated[
        datetime | None,
        typer.Option("--until", formats=["%Y-%m-%d"], help="Only up to this date."),
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Stop after this many executions.")
    ] = None,
    no_answers: Annotated[
        bool,
        typer.Option("--no-answers", help="Skip answers; fetch delivery status only."),
    ] = False,
    against: Annotated[
        Path | None,
        typer.Option(
            "--against",
            help="Sheet export to reconcile against; writes only missing rows.",
        ),
    ] = None,
    key_column: Annotated[
        str, typer.Option("--key-column", help="Sheet column to match on.")
    ] = "execution_sid",
    to_motherduck: Annotated[
        str | None,
        typer.Option("--to-motherduck", help="Also load into this MotherDuck table."),
    ] = None,
    database: Annotated[
        str | None, typer.Option("--database", help="MotherDuck database.")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Pull executions from Twilio, to reconcile against the Google Sheet."""
    configure(verbose)
    cfg.load_env()
    client, conf = _client()

    try:
        resolved_flow = conf.resolve_flow_id(flow_id)
    except cfg.ConfigError as exc:
        _fail(str(exc))

    try:
        frame = fetch_executions(
            client=client,
            flow_id=resolved_flow,
            date_from=since,
            date_to=until,
            limit=limit,
            include_answers=not no_answers,
        )

        if against is not None:
            sheet = (
                pd.read_csv(against, dtype=str)
                if against.suffix.lower() == ".csv"
                else pd.read_excel(against, dtype=str)
            )
            frame = reconcile(executions=frame, sheet=sheet, sheet_key=key_column)

        if frame.empty:
            typer.secho("Nothing to write.", fg=typer.colors.GREEN)
            return

        write_output(frame, output)
    except FetchError as exc:
        _fail(str(exc))

    if to_motherduck:
        try:
            push_dataframe(
                frame=frame,
                table=to_motherduck,
                database=resolve_database(database),
            )
        except WarehouseError as exc:
            _fail(str(exc))


@app.command()
def push(
    input_file: Annotated[Path, typer.Argument(help="Dataset to load.")],
    table: Annotated[str, typer.Option("--table", "-t", help="Target table name.")],
    database: Annotated[
        str | None, typer.Option("--database", help="MotherDuck database.")
    ] = None,
    mode: Annotated[
        str,
        typer.Option("--mode", help="replace, append, or create."),
    ] = "replace",
    columns: Annotated[
        str,
        typer.Option("--columns", help="Restrict the load to these columns."),
    ] = "",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Load a dataset into MotherDuck, IPA's analysis warehouse.

    Decrypted survey data is Confidential under IPA's data classification.
    Push it only to a database approved and access-controlled for that, and
    consider --columns to leave direct identifiers behind.
    """
    configure(verbose)
    cfg.load_env()

    subset = [c.strip() for c in columns.split(",") if c.strip()]
    try:
        push_file(
            path=input_file,
            table=table,
            database=resolve_database(database),
            mode=mode,
            columns=subset or None,
        )
    except WarehouseError as exc:
        _fail(str(exc))


flow_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect Studio flows and version-control their definitions.",
)
app.add_typer(flow_app, name="flow")


@flow_app.command("list")
def flow_list(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """List the account's Studio flows."""
    configure(verbose)
    cfg.load_env()
    client, _ = _client()

    try:
        flows = list_flows(client)
    except FlowError as exc:
        _fail(str(exc))

    typer.echo(f"{len(flows)} flow(s):\n")
    for flow in flows:
        updated = f"{flow.date_updated:%Y-%m-%d}" if flow.date_updated else "-"
        colour = (
            typer.colors.GREEN if flow.status == "published" else typer.colors.YELLOW
        )
        typer.echo(f"  {flow.sid}  ", nl=False)
        typer.secho(f"{flow.status:10}", fg=colour, nl=False)
        typer.echo(f"rev {flow.revision:<5} {updated}  {flow.friendly_name}")


@flow_app.command("pull")
def flow_pull(
    identifier: Annotated[str, typer.Argument(help="Flow SID or friendly name.")],
    destination: Annotated[
        Path, typer.Option("--out", "-o", help="Directory to write into.")
    ] = Path("flows"),
    allow_secrets: Annotated[
        bool,
        typer.Option(
            "--allow-secrets",
            help="Write even if the definition looks like it contains credentials.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Save a flow definition as JSON so it can be reviewed and diffed."""
    configure(verbose)
    cfg.load_env()
    client, _ = _client()

    try:
        path = pull_flow(
            client=client,
            identifier=identifier,
            destination=destination,
            allow_secrets=allow_secrets,
        )
    except FlowError as exc:
        _fail(str(exc))

    definition = json.loads(path.read_text(encoding="utf-8"))["definition"]
    info = summarize(definition)

    typer.secho(f"\nWrote {path}", fg=typer.colors.GREEN, bold=True)
    typer.echo(
        f"  {sum(info['widget_counts'].values())} widgets, "
        f"{len(info['questions'])} question(s)"
    )

    if not info["encrypting"] and info["functions"]:
        typer.secho(
            "  No widget named like an encryption step. If this flow publishes "
            "PII\n  to Google Sheets, it is doing so in plain text.",
            fg=typer.colors.YELLOW,
        )

    stranded = unpublished_paths(definition)
    if stranded:
        typer.secho(
            f"\n  {len(stranded)} break-off path(s) never reach the publish widget:",
            fg=typer.colors.RED,
            bold=True,
        )
        for widget, event, destination in stranded[:8]:
            typer.echo(f"    {widget} --{event}--> {destination}")
        if len(stranded) > 8:
            typer.echo(f"    ... and {len(stranded) - 8} more")
        typer.secho(
            "  A respondent leaving by these produces no row at all, so a\n"
            "  break-off looks identical to someone never contacted.",
            fg=typer.colors.YELLOW,
        )

    columns = published_columns(definition)
    answers = [c for c, source in columns if source == "answer"]
    if answers:
        unpaired = unpaired_answers(definition)
        typer.echo(f"\n  {len(columns)} published column(s), {len(answers)} answer(s)")
        if unpaired:
            typer.secho(
                f"  {len(unpaired)} answer(s) publish with no status column beside "
                f"them,\n  so a blank cell cannot be read as timed-out vs "
                f"not-asked vs failed.",
                fg=typer.colors.YELLOW,
            )


@flow_app.command("check")
def flow_check(
    identifier: Annotated[
        str | None,
        typer.Argument(help="Flow SID or name. Omit to check every flow."),
    ] = None,
    errors_only: Annotated[
        bool, typer.Option("--errors-only", help="Hide warnings.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Check flows the way high-frequency checks check a survey.

    This does not look at collected data. It verifies the instrument was coded
    correctly - that every break-off still publishes a row, every question
    handles non-response, and identifiers are encrypted - so that the data it
    produces will be analysable. Run before a round and after any edit.
    """
    configure(verbose)
    cfg.load_env()
    client, _ = _client()

    try:
        targets = (
            [resolve_flow(client, identifier)]
            if identifier
            else [resolve_flow(client, f.sid) for f in list_flows(client)]
        )
    except FlowError as exc:
        _fail(str(exc))

    total_errors = total_warnings = clean = 0

    for flow in targets:
        if not flow.definition:
            continue
        # Knowing whether a template is a list picker takes one Content API
        # call per template. Worth it for a single flow; not worth 100 calls
        # when sweeping the whole account, where the checks that need it are
        # skipped rather than guessed at.
        content_types = (
            referenced_content_types(client, flow.definition) if identifier else None
        )
        findings = check_flow(flow.definition, content_types)
        if errors_only:
            findings = [f for f in findings if f.severity == "error"]

        errors = sum(1 for f in findings if f.severity == "error")
        warnings = len(findings) - errors
        total_errors += errors
        total_warnings += warnings

        if not findings:
            clean += 1
            if identifier:
                typer.secho(
                    f"{flow.friendly_name}: all checks passed", fg=typer.colors.GREEN
                )
            continue

        typer.echo("")
        typer.secho(f"{flow.friendly_name}  ({flow.status})", bold=True)
        for finding in findings:
            colour = (
                typer.colors.RED if finding.severity == "error" else typer.colors.YELLOW
            )
            typer.secho(f"  [{finding.severity}] {finding.code}", fg=colour, nl=False)
            typer.echo(f"  {finding.summary}")
            for line in finding.detail:
                typer.echo(f"      {line}")

    if not identifier:
        typer.echo("")
        typer.secho(
            f"{len(targets)} flow(s): {clean} clean, "
            f"{total_errors} error(s), {total_warnings} warning(s)",
            bold=True,
        )

    if total_errors:
        raise typer.Exit(code=1)


@flow_app.command("deploy")
def flow_deploy(
    definition_file: Annotated[
        Path, typer.Argument(help="Flow definition JSON, e.g. flows/foo.json")
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help="Friendly name. Defaults to the file stem."),
    ] = None,
    publish: Annotated[
        bool, typer.Option("--publish", help="Publish rather than leaving a draft.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Deploy despite check errors. Rarely right."),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Deploy a flow, refusing to ship one that fails the checks.

    The gate matters because this class of defect spreads by duplication:
    seven flows on this account carry one identical break-off path that never
    reaches the publish widget, copied six times when flows were cloned. A
    check you have to remember to run does not prevent that.
    """
    configure(verbose)
    cfg.load_env()
    client, _ = _client()

    if not definition_file.is_file():
        _fail(f"No such file: {definition_file}")

    try:
        payload = json.loads(definition_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"{definition_file} is not valid JSON: {exc}")

    # Accept either a bare definition or the wrapper `rtt flow pull` writes.
    definition = payload.get("definition", payload)
    flow_name = name or payload.get("friendly_name") or definition_file.stem

    try:
        sid, findings = deploy_flow(
            client=client,
            name=flow_name,
            definition=definition,
            publish=publish,
            force=force,
        )
    except FlowError as exc:
        _fail(str(exc))

    for finding in findings:
        colour = (
            typer.colors.RED if finding.severity == "error" else typer.colors.YELLOW
        )
        typer.secho(
            f"  [{finding.severity}] {finding.code}  {finding.summary}", fg=colour
        )

    state = "published" if publish else "draft"
    typer.echo("")
    typer.secho(f"{flow_name}  {sid}  ({state})", fg=typer.colors.GREEN, bold=True)


template_app = typer.Typer(
    no_args_is_help=True,
    help="Create WhatsApp templates and submit them to Meta for approval.",
)
app.add_typer(template_app, name="template")


def _resolve_content(client: Client, identifier: str):
    """Find a content template by SID or friendly name."""
    if identifier.startswith("HX"):
        return client.content.v1.contents(identifier).fetch()
    content = find_by_name(client, identifier)
    if content is None:
        _fail(
            f"No template named {identifier!r}. Run `rtt template list` to see "
            "what exists."
        )
    return content


@template_app.command("list")
def template_list(
    name_filter: Annotated[
        str,
        typer.Option("--filter", "-f", help="Match friendly names containing this."),
    ] = "",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """List content templates and their WhatsApp approval status."""
    configure(verbose)
    cfg.load_env()
    client, _ = _client()

    try:
        rows = list_templates(client, name_filter or None)
    except TemplateError as exc:
        _fail(str(exc))

    if not rows:
        typer.echo("No templates match.")
        return

    typer.echo(f"{len(rows)} template(s):\n")
    for row in rows:
        colour = (
            typer.colors.GREEN
            if row["status"] == "approved"
            else typer.colors.YELLOW
            if row["status"] in {"pending", "received"}
            else typer.colors.RED
            if row["status"] == "rejected"
            else typer.colors.WHITE
        )
        typer.echo(f"  {row['sid']}  ", nl=False)
        typer.secho(f"{row['status']:12}", fg=colour, nl=False)
        typer.echo(f"{row['language']:7} {row['friendly_name']}")


def _create_one(
    client: Client,
    definition_file: Path,
    *,
    submit: bool,
    category: str,
    yes: bool,
    skip_existing: bool,
) -> None:
    """Create one template from its definition file."""
    try:
        definition = load_definition(definition_file)
    except TemplateError as exc:
        _fail(str(exc))

    name = definition["friendly_name"]

    existing = find_by_name(client, name)
    if existing is not None:
        if skip_existing:
            typer.echo(f"exists  {name}  ({existing.sid})")
            return
        _fail(
            f"A template named {name!r} already exists ({existing.sid}). "
            "Template names should be unique; pick a new name in the definition."
        )

    for warning in check_variables(definition):
        typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW)

    typer.echo(f"\nAbout to create {name!r} ({definition['language']}):\n")
    for type_name, body in definition["types"].items():
        typer.secho(f"  {type_name}", bold=True)
        text = body.get("body") or body.get("title") or ""
        for line in text.splitlines():
            typer.echo(f"    {line}")
        for action in body.get("actions") or []:
            typer.echo(f"    [{action.get('title')}]")
        for item in body.get("items") or []:
            typer.echo(f"    - {item.get('item')}  ({item.get('description')})")

    if submit:
        typer.secho(
            "\n  --submit given: this will also be sent to Meta for approval.\n"
            "  Submitted templates CANNOT be edited, ever. Changing a word later\n"
            "  means a new template and a new review.",
            fg=typer.colors.RED,
            bold=True,
        )

    if not yes and not typer.confirm("\nProceed?"):
        typer.echo("Aborted.")
        raise typer.Exit(code=1)

    try:
        content = create_template(client, definition)
    except TemplateError as exc:
        _fail(str(exc))

    typer.secho(f"\nCreated {content.sid}", fg=typer.colors.GREEN, bold=True)

    if submit:
        try:
            submit_template(client, content.sid, name, category)
        except TemplateError as exc:
            _fail(str(exc))
        typer.secho(
            f"Submitted to Meta as {category}. Check with:\n"
            f"  just template-status {name}",
            fg=typer.colors.GREEN,
        )
    else:
        typer.echo(
            f"\nNot yet submitted to Meta - it exists only in Twilio, so it can\n"
            f"still be deleted and redone. When the wording is final:\n"
            f"  just template-submit {name}"
        )


@template_app.command("create")
def template_create(
    definition_file: Annotated[
        Path,
        typer.Argument(
            help="JSON definition, e.g. templates/foo.json. A directory creates "
            "every *.json in it."
        ),
    ],
    submit: Annotated[
        bool,
        typer.Option("--submit", help="Also submit to Meta. Irreversible."),
    ] = False,
    category: Annotated[
        str, typer.Option("--category", help=f"One of: {', '.join(CATEGORIES)}")
    ] = "UTILITY",
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")
    ] = False,
    skip_existing: Annotated[
        bool,
        typer.Option(
            "--skip-existing",
            help="Report and exit 0 if the name is already taken, instead of "
            "failing. For scripting over a directory of definitions.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Create one template, or every template in a directory.

    Taking a directory here rather than looping in the shell keeps the recipe
    that calls it portable: a `for` loop in a Justfile needs a bash shebang,
    which on Windows needs cygpath to translate.
    """
    configure(verbose)

    if definition_file.is_dir():
        paths = sorted(definition_file.glob("*.json"))
        if not paths:
            _fail(f"No .json definitions in {definition_file}")
        if submit:
            # Bulk-submitting is irreversible for every file at once, and the
            # whole point of reviewing wording is doing it one at a time.
            _fail(
                "Refusing to --submit a whole directory. Submission is "
                "irreversible; submit each template by name once its wording "
                "is final."
            )
    else:
        paths = [definition_file]

    cfg.load_env()
    client, _ = _client()

    for path in paths:
        _create_one(
            client,
            path,
            submit=submit,
            category=category,
            yes=yes,
            skip_existing=skip_existing,
        )


@template_app.command("delete")
def template_delete(
    identifier: Annotated[str, typer.Argument(help="Content SID or friendly name.")],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Delete an unsubmitted template, so its wording can be redone.

    Twilio has no update operation for content, so revising a template that has
    not gone to Meta yet means deleting it and creating it again. This is what
    makes the create/submit split genuinely reversible on the create side.

    Refuses anything that has been submitted. A template Meta has seen may be
    referenced by a flow that is running right now, and deleting it breaks that
    flow silently - if you really mean to retire one, do it in the console where
    the consequences are in front of you.
    """
    configure(verbose)
    cfg.load_env()
    client, _ = _client()

    content = _resolve_content(client, identifier)

    try:
        status = approval_status(client, content.sid)["status"]
    except TemplateError:
        # No approval record at all is the normal case for a template that was
        # created and never submitted.
        status = "unsubmitted"

    if status.lower() not in ("unsubmitted", "unknown"):
        _fail(
            f"{content.friendly_name!r} has been submitted to Meta (status: "
            f"{status}). Refusing to delete it here: a flow may be using it, "
            "and deleting it would break that flow without warning. Retire it "
            "in the Twilio console if that is really what you want."
        )

    typer.secho(
        f"\nAbout to DELETE {content.friendly_name!r} ({content.sid})",
        fg=typer.colors.YELLOW,
        bold=True,
    )
    typer.echo(
        "  Any flow referencing this SID will stop working until it is\n"
        "  rebuilt against the replacement."
    )

    if not yes and not typer.confirm("\nDelete it?"):
        typer.echo("Aborted.")
        raise typer.Exit(code=1)

    try:
        delete_template(client, content.sid)
    except TemplateError as exc:
        _fail(str(exc))

    typer.secho(f"Deleted {content.sid}", fg=typer.colors.GREEN, bold=True)
    typer.echo(
        "\nThe name is free again. Recreate it with:\n"
        f"  just template-create templates/{content.friendly_name}.json"
    )


@template_app.command("submit")
def template_submit(
    identifier: Annotated[str, typer.Argument(help="Content SID or friendly name.")],
    category: Annotated[
        str, typer.Option("--category", help=f"One of: {', '.join(CATEGORIES)}")
    ] = "UTILITY",
    meta_name: Annotated[
        str | None,
        typer.Option("--name", help="Name Meta sees. Defaults to the friendly name."),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Submit an existing template to Meta for WhatsApp approval."""
    configure(verbose)
    cfg.load_env()
    client, _ = _client()

    content = _resolve_content(client, identifier)
    name = meta_name or content.friendly_name

    typer.secho(
        f"Submitting {content.friendly_name!r} ({content.sid}) to Meta\n"
        f"  name:     {name}\n"
        f"  category: {category}\n",
        bold=True,
    )
    typer.secho(
        "  This cannot be undone. Meta has no edit operation: once submitted,\n"
        "  the wording is frozen and any change needs a new template.",
        fg=typer.colors.RED,
    )

    if not yes and not typer.confirm("\nSubmit?"):
        typer.echo("Aborted.")
        raise typer.Exit(code=1)

    try:
        submit_template(client, content.sid, name, category)
    except TemplateError as exc:
        _fail(str(exc))

    typer.secho("Submitted.", fg=typer.colors.GREEN, bold=True)
    typer.echo(
        "Most templates are reviewed within minutes. Check with:\n"
        f"  just template-status {content.friendly_name}"
    )


@template_app.command("status")
def template_status(
    identifier: Annotated[str, typer.Argument(help="Content SID or friendly name.")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Show a template's WhatsApp approval status."""
    configure(verbose)
    cfg.load_env()
    client, _ = _client()

    content = _resolve_content(client, identifier)

    try:
        status = approval_status(client, content.sid)
    except TemplateError as exc:
        _fail(str(exc))

    typer.echo(f"{content.friendly_name}  ({content.sid})")
    typer.echo(f"  status:   {status['status']}")
    if status.get("category"):
        typer.echo(f"  category: {status['category']}")
    if status.get("rejection_reason"):
        typer.secho(f"  rejected: {status['rejection_reason']}", fg=typer.colors.RED)


def main() -> None:
    """Entry point used by the ``rtt`` script."""
    try:
        app()
    except KeyboardInterrupt:
        typer.secho("\nInterrupted.", fg=typer.colors.YELLOW, err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
