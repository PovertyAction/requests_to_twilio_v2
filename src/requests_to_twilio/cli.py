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
import time
from datetime import UTC, datetime
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
    ACCOUNT_ONLY_CHECKS,
    TOTAL_CHECKS,
    FlowError,
    check_flow,
    check_preloaded,
    inbound_flow_sid,
    list_flows,
    load_definition_file,
    published_columns,
    published_revision,
    referenced_content_types,
    resolve_flow,
    sheet_header_row,
    summarize,
    unpaired_answers,
    unpublished_paths,
    warehouse_schema,
)
from .flows import (
    deploy as deploy_flow,
)
from .flows import pull as pull_flow
from .hfc import check_dataset, outcome_counts
from .launcher import SENT_AT_PARAM, LaunchError, launch
from .log import configure, configure_output_encoding
from .monitor import (
    MonitorError,
    by_number,
    launch_failures,
    launch_window,
    pending,
    poll_delivery,
    read_master_list,
    read_tracker,
    update_log,
)
from .sheets import (
    TOKEN_LIFETIME_SECONDS,
    SheetsError,
    access_token,
    credentials_from_env,
    replace_tab,
)
from .spec import (
    SCOPE_NOTE,
    SpecError,
    check_spec,
    load_spec,
    review_notes,
    save_spec,
    starter_spec,
)
from .spec_xlsx import read_xlsx, write_xlsx
from .templates import (
    CATEGORIES,
    TemplateError,
    approval_status,
    check_variables,
    drifted_types,
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
from .warehouse import (
    ENV_PUBLISH_TABLE,
    WarehouseError,
    push_dataframe,
    push_file,
    resolve_database,
)

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
    # `sent_at` likewise: the launcher supplies it on every send, so a flow may
    # reference it without anyone adding a column for it.
    missing, unused = check_preloaded(
        flow.definition, set(columns) | {"Number", SENT_AT_PARAM}
    )

    # ...which also means it is never a preload, so reporting it as an unused
    # one is noise on every single run. A warning that always fires is a
    # warning nobody reads.
    unused = unused - {"Number", SENT_AT_PARAM}

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
            f"           WhatsApp, set {cfg.ENV_FROM_NUMBER} to "
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


def _print_findings(label: str, findings: list) -> None:
    """Render one flow's findings. Errors red, warnings yellow, clean green."""
    if not findings:
        typer.secho(f"{label}: all checks passed", fg=typer.colors.GREEN)
        return

    typer.echo("")
    typer.secho(label, bold=True)
    for finding in findings:
        colour = (
            typer.colors.RED if finding.severity == "error" else typer.colors.YELLOW
        )
        typer.secho(f"  [{finding.severity}] {finding.code}", fg=colour, nl=False)
        typer.echo(f"  {finding.summary}")
        for line in finding.detail:
            typer.echo(f"      {line}")


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
        Path, typer.Argument(help="Collected dataset: a warehouse or sheet export.")
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
                # Explicit, and deliberately not the `append` default. This
                # loads a whole decrypted export into a derived table, so it is
                # a snapshot: decrypting Tuesday's file and then Wednesday's
                # would otherwise leave the table holding both, most of it the
                # same respondents twice.
                mode="replace",
            )
        except WarehouseError as exc:
            _fail(str(exc))


@app.command()
def fetch(
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Where to write. Defaults to executions.csv, or missing.csv "
                "with --against."
            ),
        ),
    ] = None,
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
    """Pull executions from Twilio, to reconcile against the published table.

    The output is unencrypted: the Studio execution context holds the answers
    as the respondent sent them, along with their number. Encryption protects
    the copy in the warehouse, never the copy inside Twilio.
    """
    configure(verbose)
    cfg.load_env()
    client, conf = _client()

    # The two modes write very different things - a full export, or only the
    # rows that are missing from it - so they get different default names. One
    # shared default meant a reconcile could silently replace a 3,000-row export
    # with a three-row diff at the same path.
    destination = output or Path("missing.csv" if against else "executions.csv")

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

        write_output(frame, destination)
    except FetchError as exc:
        _fail(str(exc))

    if to_motherduck:
        try:
            push_dataframe(
                frame=frame,
                table=to_motherduck,
                database=resolve_database(database),
                # A snapshot of what Twilio currently believes, not an
                # accumulation. `rtt fetch` re-walks the whole retention window
                # on every run and is meant to be run repeatedly during a
                # round, so appending would duplicate every overlapping
                # execution each time.
                mode="replace",
            )
        except WarehouseError as exc:
            _fail(str(exc))


def _same_table(candidate: str, published: str) -> bool:
    """Whether two table names refer to the same table.

    `MOTHERDUCK_TABLE` is fully qualified (`db.main.round`) while `--table` is
    usually bare, so comparing the strings would miss the collision this exists
    to catch. The final segment is what identifies the table within a database,
    and a bare name is matched against it.

    Args:
        candidate: The name passed on the command line.
        published: The publish target, from the environment.

    Returns:
        True when writing to `candidate` would overwrite `published`.

    """

    def leaf(name: str) -> str:
        return name.strip().strip('"').split(".")[-1].strip('"').casefold()

    return leaf(candidate) == leaf(published)


@app.command("monitor")
def monitor(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="The running log to create or update."),
    ] = Path("delivery_log.csv"),
    tracker: Annotated[
        Path | None,
        typer.Option(
            "--tracker",
            help="A delivery tracker from `rtt launch`. Scopes the poll to that round.",
        ),
    ] = None,
    since: Annotated[
        datetime | None,
        typer.Option(
            "--since", formats=["%Y-%m-%d"], help="Only from this date. Overrides."
        ),
    ] = None,
    until: Annotated[
        datetime | None,
        typer.Option("--until", formats=["%Y-%m-%d"], help="Only up to this date."),
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Stop after this many messages.")
    ] = None,
    sample: Annotated[
        Path | None,
        typer.Option(
            "--sample",
            help=(
                "The master list the round was launched from. Turns phone "
                "numbers into caseids; inferred from --tracker when omitted."
            ),
        ),
    ] = None,
    hours: Annotated[
        float | None,
        typer.Option("--hours", help="Keep polling for this long. Omit to poll once."),
    ] = None,
    every: Annotated[int, typer.Option("--every", help="Minutes between polls.")] = 30,
    full_window: Annotated[
        bool,
        typer.Option(
            "--full-window",
            help=(
                "Keep polling for the whole --hours window, even once every "
                "number has settled. For a tab somebody is watching live."
            ),
        ),
    ] = False,
    sheet: Annotated[
        bool,
        typer.Option(
            "--sheet", help="Also rewrite a Google Sheet tab after every poll."
        ),
    ] = False,
    sheet_tab: Annotated[
        str,
        typer.Option("--sheet-tab", help="Which tab to rewrite."),
    ] = "tracking",
    table: Annotated[
        str | None,
        typer.Option(
            "--table",
            help="Also replace a MotherDuck table after every poll, e.g. tracking.",
        ),
    ] = None,
    database: Annotated[
        str | None,
        typer.Option(
            "--database", help="MotherDuck database. Defaults to MOTHERDUCK_DATABASE."
        ),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Watch a round land: one row per number, polled until it settles.

    We launched - did it arrive, and did they answer? Each number holds one
    state, in order of progress:

        failed          the opener did not go out, or came back undelivered
        sent            accepted by Twilio, not yet confirmed on the handset
        delivered       it arrived
        answered_back   they replied, so the flow has taken over

    `failed` and `answered_back` are final and stop being polled: a failure does
    not un-fail, and once someone is answering, their progress is a question for
    `rtt fetch`, not for delivery status. When every number has settled the loop
    stops on its own rather than spending rate limit on a finished round.

    **That is not the same as the survey finishing**, and it is why
    `--full-window` exists. Somebody who has `answered_back` is still working
    through the questions; delivery simply has nothing further to say about them.
    So on a prompt round every number settles within a minute or two and the loop
    exits - fine when reconciling afterwards, useless when the point is a
    spreadsheet tab moving in front of a room. With `--full-window` the loop
    keeps polling until `--hours` is up regardless.

    Reads the layer `rtt fetch` and `rtt data-check` cannot see. A send that Meta
    rejects never becomes an execution and never publishes a row, so that person
    is absent from the data rather than incomplete in it.

    Pass `--tracker` to scope to one round. A date is the wrong unit: on the
    first live round of this instrument `--since` at day resolution returned 91
    messages for a round of 4.

    `--sheet` mirrors each poll into a spreadsheet tab and `--table` into a
    MotherDuck table. Neither is the record - the CSV at `--output` is, and it
    is written first - so a destination that will not accept a write reports it
    and the round keeps being watched. Both are independent of where the flow
    publishes: this command reads the Messages API, so a round publishing to
    Sheets can be tracked in MotherDuck and the other way round.

    `--table` is REPLACED on every poll, because one row per number is a
    current state rather than a log. Pointed at the table the flow publishes to
    it would drop the round's submissions and report success, so a `--table`
    matching `MOTHERDUCK_TABLE` is refused.
    """
    configure(verbose=verbose)
    cfg.load_env()
    conf = cfg.TwilioConfig.from_env()

    launched: pd.DataFrame | None = None
    try:
        if tracker is not None:
            launched = read_tracker(tracker)
            # An explicit --since wins: someone narrowing a window by hand has a
            # reason, and silently overriding it would be worse than ignoring it.
            if since is None:
                since = launch_window(launched)
                if since is None:
                    _fail(
                        f"{tracker} has no usable sent_at, so the round has no start."
                    )
                typer.echo(f"Round launched {since.isoformat()} (from {tracker.name})")

        # Before anything else: these never became messages, so they cannot
        # appear in any delivery status below.
        never_sent = launch_failures(launched) if launched is not None else None
        if never_sent is not None and not never_sent.empty:
            typer.secho(
                f"{len(never_sent)} send(s) never left - no execution, no row:",
                fg=typer.colors.RED,
            )
            for _, row in never_sent.iterrows():
                typer.echo(f"  {row.get('caseid', '?')}  {row.get('error', '')}")
            typer.echo("")

        # The master list is the only file this command reads a phone number
        # from, and the mapping never leaves memory. Without it every row would
        # be filed under `unknown-<digest>`, which is technically safe and
        # useless to watch, so say so rather than degrade quietly.
        if sample is None and tracker is not None:
            guess = tracker.with_name(tracker.name.replace("_output.csv", ".xlsx"))
            if guess != tracker and guess.is_file():
                sample = guess
                typer.echo(f"Master list {sample.name} (inferred from --tracker)")
        caseids = read_master_list(sample) if sample is not None else {}
        if not caseids:
            typer.secho(
                "No master list given, so every respondent will be filed under "
                "an `unknown-` key. Pass --sample to see caseids.",
                fg=typer.colors.YELLOW,
            )

        warehouse_db = None
        if table is not None:
            try:
                warehouse_db = resolve_database(database)
            except WarehouseError as exc:
                _fail(str(exc))
            # Every poll issues CREATE OR REPLACE TABLE, because what this
            # mirrors is the current state rather than a log. Pointed at the
            # table the flow publishes to, that drops the round's submissions on
            # the first poll and again every two minutes, and the terminal would
            # report each one as a successful update. The publish target is
            # named in the environment, so this is checkable rather than a
            # matter of remembering.
            published = cfg.optional(ENV_PUBLISH_TABLE) or ""
            if published and _same_table(table, published):
                _fail(
                    f"--table {table} is where the flow publishes ({published}). "
                    "Each poll would replace the round's data. Use a separate "
                    "table, e.g. --table tracking."
                )
            typer.echo(f"Publishing each poll to {warehouse_db}.{table}")

        token = None
        minted_at = 0.0
        if sheet:
            email, key, sheet_id = credentials_from_env()
            token = access_token(email, key)
            minted_at = time.monotonic()
            typer.echo(f"Publishing each poll to tab {sheet_tab!r}")

        client = Client(conf.account_sid, conf.auth_token)
        deadline = None if hours is None else time.monotonic() + hours * 3600

        while True:
            frame = poll_delivery(client=client, since=since, until=until, limit=limit)
            added, changed = update_log(by_number(frame, caseids), output)

            # Read back rather than trust this poll: the log carries numbers a
            # narrower window did not cover, and settled rows it refused to move.
            log = (
                pd.read_csv(output, dtype=str).fillna("")
                if output.is_file()
                else by_number(frame, caseids)
            )
            waiting = pending(log)

            counts = log["delivery_status"].value_counts().to_dict()
            stamp = datetime.now(UTC).strftime("%H:%M:%S")
            summary = "  ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            typer.echo(f"[{stamp}Z] {summary}   ({added} new, {changed} changed)")

            for _, row in log[log["error_codes"] != ""].iterrows():
                typer.secho(
                    f"    {row['caseid']}  error {row['error_codes']}"
                    "  reply reached Twilio and was dropped - check the webhook",
                    fg=typer.colors.YELLOW,
                )

            if token is not None:
                # A Google assertion is minted for TOKEN_LIFETIME_SECONDS, which
                # is ten minutes. That was ample while a settled round exited on
                # the first poll; `--full-window --hours 1` holds the loop open
                # for sixty, so the token died a sixth of the way in and every
                # later write returned 401. The failure is swallowed below by
                # design, so the tracking tab - the whole reason --full-window
                # exists - froze for the remaining fifty minutes while the
                # terminal printed "all settled, still watching" and the command
                # exited green.
                #
                # Re-mint with a poll's margin, and retry once on a fresh token
                # so a write that straddles the boundary still lands.
                age = time.monotonic() - minted_at
                if age > TOKEN_LIFETIME_SECONDS - max(every * 60, 60):
                    try:
                        token = access_token(email, key)
                        minted_at = time.monotonic()
                    except SheetsError as exc:
                        typer.secho(
                            f"    could not refresh the sheet token: {exc}",
                            fg=typer.colors.YELLOW,
                        )
                try:
                    rows, _ = replace_tab(
                        log, sheet_id=sheet_id, tab=sheet_tab, token=token
                    )
                    typer.echo(f"    sheet updated: {rows} row(s) in {sheet_tab!r}")
                except SheetsError as exc:
                    retried = False
                    try:
                        token = access_token(email, key)
                        minted_at = time.monotonic()
                        rows, _ = replace_tab(
                            log, sheet_id=sheet_id, tab=sheet_tab, token=token
                        )
                        retried = True
                        typer.echo(f"    sheet updated: {rows} row(s) in {sheet_tab!r}")
                    except SheetsError:
                        pass
                    if not retried:
                        # A sheet that will not update must not end the round's
                        # monitoring. The CSV is already written and is the
                        # record; the sheet is a view of it.
                        typer.secho(
                            f"    sheet not updated: {exc}", fg=typer.colors.YELLOW
                        )

            if warehouse_db is not None:
                # The MotherDuck counterpart of the sheet write above, without
                # its failure mode: the token is read from the environment on
                # each connection rather than minted for ten minutes, so there
                # is nothing here that goes stale part-way through a window.
                try:
                    written = push_dataframe(
                        frame=log,
                        table=table,
                        database=warehouse_db,
                        mode="replace",
                    )
                    typer.echo(f"    table updated: {written} row(s) in {table}")
                except WarehouseError as exc:
                    # Same rule as the sheet: the CSV is the record and the
                    # table is a view of it, so a failed write reports itself
                    # and the round keeps being watched.
                    typer.secho(f"    table not updated: {exc}", fg=typer.colors.YELLOW)

            settled_message = "\nEvery number has settled - nothing left to watch."

            if waiting.empty and not full_window:
                typer.secho(settled_message, fg=typer.colors.GREEN)
                break
            if deadline is None:
                # No window asked for, so one poll was the whole job. Reached
                # with --full-window too: keeping a window open needs a window.
                if waiting.empty:
                    typer.secho(settled_message, fg=typer.colors.GREEN)
                else:
                    typer.echo(
                        f"{len(waiting)} still pending. Pass --hours to keep watching."
                    )
                break
            if time.monotonic() >= deadline:
                if waiting.empty:
                    typer.secho(
                        "\nWindow closed, every number settled.", fg=typer.colors.GREEN
                    )
                else:
                    typer.secho(
                        f"\nWindow closed with {len(waiting)} still pending: "
                        + ", ".join(str(c) for c in waiting["caseid"]),
                        fg=typer.colors.YELLOW,
                    )
                break

            state = (
                f"{len(waiting)} pending"
                if not waiting.empty
                else "all settled, still watching"
            )
            typer.echo(f"    {state}, next poll in {every} min")
            time.sleep(every * 60)
    except MonitorError as exc:
        _fail(str(exc))
    except KeyboardInterrupt:
        # The log is written after every poll, so stopping loses nothing.
        typer.secho(f"\nStopped. {output} holds the last poll.", fg=typer.colors.YELLOW)


@app.command("data-check")
def data_check(
    input_file: Annotated[
        Path, typer.Argument(help="Collected dataset (.csv or .xlsx).")
    ],
    key: Annotated[
        str, typer.Option("--key", help="Respondent identifier column.")
    ] = "caseid",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """High-frequency checks on collected data, during a round.

    `rtt flow check` verifies the instrument was coded correctly before a round.
    This is the other half: some defects only exist in the data. A respondent
    who answered twice looks perfectly healthy in every widget of the flow, and
    is double-weighted in every mean computed afterwards.
    """
    configure(verbose)

    try:
        if input_file.suffix.lower() in {".xlsx", ".xlsm"}:
            frame = pd.read_excel(input_file, dtype=str)
        else:
            frame = pd.read_csv(input_file, dtype=str)
    except Exception as exc:  # noqa: BLE001 - any read failure is the same to us
        _fail(f"Could not read {input_file}: {exc}")

    typer.echo(f"{input_file}: {len(frame)} row(s)")

    outcomes = outcome_counts(frame)
    if outcomes:
        typer.echo("  outcomes: " + ", ".join(f"{k}={v}" for k, v in outcomes.items()))

    findings = check_dataset(frame, key)
    if not findings:
        typer.secho("  all checks passed", fg=typer.colors.GREEN)
        return

    for finding in findings:
        colour = (
            typer.colors.RED if finding.severity == "error" else typer.colors.YELLOW
        )
        typer.secho(f"  [{finding.severity}] {finding.code}", fg=colour, bold=True)
        typer.echo(f"      {finding.summary}")
        for line in finding.detail[:10]:
            typer.echo(f"        {line}")

    # Deliberately exits zero on warnings. This reports on data that already
    # exists, so there is nothing left to prevent - and it is meant to be run on
    # a loop while a round is live, which a non-zero exit would break. A
    # duplicate may be a defect or a deliberate re-launch; only the person
    # running the round knows which.
    if any(f.severity == "error" for f in findings):
        raise typer.Exit(code=1)


@app.command()
def push(
    input_file: Annotated[Path, typer.Argument(help="Dataset to load.")],
    table: Annotated[str, typer.Option("--table", "-t", help="Target table name.")],
    database: Annotated[
        str | None, typer.Option("--database", help="MotherDuck database.")
    ] = None,
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="append (default), replace, or create. replace DROPS the table.",
        ),
    ] = "append",
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

    The default is --mode append. `replace` issues CREATE OR REPLACE TABLE, so
    pointing it at the table the flow publishes to destroys the round's data;
    that has to be asked for explicitly.
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
        typer.Argument(
            help=(
                "Flow SID, name, or a local definition file. "
                "Omit to check every flow on the account."
            )
        ),
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

    # A local file needs no credentials and no network, which matters: this is
    # the check you want to run on a flow you have just generated and not yet
    # deployed, and in CI where there are no Twilio credentials at all.
    local = Path(identifier) if identifier else None
    if local is not None and local.is_file():
        try:
            definition = load_definition_file(local)
        except FlowError as exc:
            _fail(str(exc))
        findings = check_flow(definition)
        if errors_only:
            findings = [f for f in findings if f.severity == "error"]
        _print_findings(local.name, findings)
        # Say what did not run. Four checks read each template's content type,
        # which only Twilio can answer, so on a local file they are skipped -
        # and "all checks passed" then means 17 of 21, which is exactly the kind
        # of quiet partial success this command exists to expose. A list-picker
        # opener passes on disk and cannot open a conversation.
        typer.secho(
            f"\n  Not run on a local file ({len(ACCOUNT_ONLY_CHECKS)} of "
            f"{TOTAL_CHECKS} checks): "
            + ", ".join(ACCOUNT_ONLY_CHECKS)
            + ".\n  These read a template's content type from the account. "
            "Re-run against the\n  deployed flow by name to exercise them.",
            fg=typer.colors.BLUE,
        )
        if any(f.severity == "error" for f in findings):
            raise typer.Exit(code=1)
        return

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


@flow_app.command("schema")
def flow_schema(
    definition_file: Annotated[
        Path, typer.Argument(help="Flow definition JSON, e.g. flows/foo.json")
    ],
    table: Annotated[
        str,
        typer.Option("--table", help="Fully qualified destination table."),
    ] = "your_database.main.your_round",
    output_format: Annotated[
        str,
        typer.Option(
            "--format",
            help=(
                "'ddl' for MotherDuck CREATE TABLE, 'header' for the "
                "spreadsheet header row publish_gsheets reads."
            ),
        ),
    ] = "ddl",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Print the destination's shape, matching what the flow publishes.

    Both publish Functions write only into columns that already exist - a table
    column for MotherDuck, a header cell for Sheets - so a question added to the
    flow lands somewhere with nowhere to put it and is dropped, silently, with a
    200 and a row that looks complete. Deriving the destination's shape from the
    instrument is what keeps the two from drifting.
    """
    configure(verbose)

    if output_format not in ("ddl", "header"):
        _fail(f"Unknown --format {output_format!r}. Use 'ddl' or 'header'.")

    try:
        # Shared with `flow check` and `flow deploy`, so all three accept both a
        # bare definition and the wrapper `flow pull` writes. They used to
        # disagree, and `flow schema` on a pulled file reported "Flow publishes
        # nothing" rather than "wrong shape".
        definition = load_definition_file(definition_file)
        if output_format == "header":
            typer.echo(sheet_header_row(definition))
        else:
            typer.echo(warehouse_schema(definition, table))
    except FlowError as exc:
        _fail(str(exc))


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
    a whole family of copied flows can carry one identical break-off path that never
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
    replace: bool = False,
) -> None:
    """Create one template from its definition file."""
    try:
        definition = load_definition(definition_file)
    except TemplateError as exc:
        _fail(str(exc))

    name = definition["friendly_name"]

    existing = find_by_name(client, name)
    if existing is not None:
        if replace:
            drift = drifted_types(existing, definition)
            if not drift:
                typer.echo(f"current {name}  ({existing.sid})")
                return
            # Never delete something Meta has reviewed. Approval attaches to the
            # SID, so replacing an approved template silently discards it and
            # the round starts failing with 63016 at send time.
            status = approval_status(client, existing.sid).get("status", "unknown")
            if status not in {"unsubmitted", "unknown"}:
                _fail(
                    f"{name!r} differs from the file but is {status} with Meta "
                    f"({existing.sid}).\n"
                    "Approval attaches to the SID, so replacing it would throw "
                    "the approval away.\n"
                    "Give the new wording a new name instead, and point the "
                    "flow at that."
                )
            typer.echo(f"replacing {name}  ({existing.sid})  {', '.join(drift)}")
            delete_template(client, existing.sid)
            existing = None
        elif skip_existing:
            typer.echo(f"exists  {name}  ({existing.sid})")
            return
        else:
            _fail(
                f"A template named {name!r} already exists ({existing.sid}). "
                "Template names should be unique; pick a new name in the "
                "definition, or pass --replace to make Twilio match the file."
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
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Make Twilio match the file: delete and recreate any template "
            "whose wording has drifted. Refuses on anything submitted to Meta.",
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
            replace=replace,
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
    """Entry point for the ``rtt`` script.

    Wired in ``pyproject.toml`` as ``rtt = "requests_to_twilio.cli:main"``. It
    used to point at ``app`` directly, which meant this function - and the
    Ctrl-C handling below - never ran: interrupting a live send printed a
    traceback rather than a message.
    """
    # Shared with the scripts under scripts/, which print the same instrument
    # text and hit the same Windows code-page failure. See the reasoning in
    # `log.configure_output_encoding`.
    configure_output_encoding()

    try:
        app()
    except KeyboardInterrupt:
        # A round is launched in batches, so Ctrl-C is a normal way to stop one.
        # The tracker is flushed per row, making the interrupted run resumable.
        typer.secho("\nInterrupted.", fg=typer.colors.YELLOW, err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()


survey_app = typer.Typer(
    no_args_is_help=True,
    help="Read and check the survey spec: the instrument as rows, not widgets.",
)
app.add_typer(survey_app, name="survey")


def _load_any_spec(path: Path):
    """Load a spec from either serialisation, choosing by extension.

    The two are the same schema, so which one a command was handed should not
    change what it does. Only `convert` cares about the difference.
    """
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return read_xlsx(path)
    return load_spec(path)


@survey_app.command("convert")
def survey_convert(
    source: Annotated[Path, typer.Argument(help="The spec to read: .json or .xlsx.")],
    destination: Annotated[
        Path, typer.Argument(help="Where to write it: .xlsx or .json.")
    ],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Convert a spec between its JSON and workbook forms.

    The JSON is the canonical copy - it is what git carries and what a reviewer
    diffs. The workbook is a view of it, regenerated whenever somebody needs to
    read or edit the instrument, and gitignored because a workbook in a pull
    request is a binary blob nobody can review.

    So the normal cycle is: convert to .xlsx, edit in Excel, convert back, and
    commit the JSON. The diff in the pull request is then the change to the
    instrument, in words.
    """
    configure(verbose)

    if source.resolve() == destination.resolve():
        _fail(f"Source and destination are the same file ({source}).")

    try:
        spec = _load_any_spec(source)
    except SpecError as exc:
        _fail(str(exc))

    suffix = destination.suffix.lower()
    to_workbook = suffix in {".xlsx", ".xlsm"}
    if not to_workbook and suffix != ".json":
        _fail(
            f"Cannot tell what to write from {destination.suffix!r}. Use .json "
            "for the canonical spec or .xlsx for the workbook."
        )

    try:
        written = (
            write_xlsx(spec, destination)
            if to_workbook
            else save_spec(spec, destination)
        )
    except SpecError as exc:
        _fail(str(exc))

    typer.secho(f"{source}  ->  {written}", fg=typer.colors.GREEN)
    if to_workbook:
        typer.echo(
            "\nThe workbook is a generated view and is gitignored. Edit it, then "
            f"convert it back:\n  rtt survey convert {written} {source}"
        )

    # Silence here would be the wrong default: a conversion is usually a step in
    # editing the instrument, and an invalid spec should be said at the point it
    # is produced rather than discovered at build time.
    problems = check_spec(spec)
    if problems:
        typer.echo("")
        typer.secho(
            f"{len(problems)} problem(s) - run `rtt survey check` for the detail.",
            fg=typer.colors.YELLOW,
        )


@survey_app.command("check")
def survey_check(
    source: Annotated[Path, typer.Argument(help="The spec to check: .json or .xlsx.")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Check a survey spec before it is compiled into a flow.

    The instrument-side equivalent of XLSForm validation, and it does more than
    read the spec: every option pattern and every constraint is *run*, through
    the same evaluator Studio uses, and checked for where each possible reply
    lands. An option a respondent can tap but the flow cannot match is not
    rejected by Twilio, by Studio, or by anything else - it just sends that
    respondent the retry nudge and records them as unable to answer.

    Exits non-zero on a problem, so it can gate a build.
    """
    configure(verbose)

    try:
        spec = _load_any_spec(source)
    except SpecError as exc:
        _fail(str(exc))

    languages = ", ".join(spec.settings.languages) or "none"
    questions = spec.questions()
    typer.secho(f"{source.name}", bold=True)
    typer.echo(
        f"  {spec.settings.form_id or 'untitled'}  |  {len(spec.survey)} rows, "
        f"{len(questions)} questions, {len(spec.choices)} options  |  {languages}"
    )
    # The claim the format makes, stated per row: this is not configuration, it
    # is a subgraph. Somebody adding a third arm should see the cost.
    typer.echo(
        f"  {spec.total_widget_count()} widgets before the shared spine "
        f"(trigger, publish, encryption)"
    )

    problems = check_spec(spec)
    if problems:
        typer.echo("")
        for problem in problems:
            typer.secho("  [error] ", fg=typer.colors.RED, nl=False)
            typer.echo(problem)
    else:
        typer.echo("")
        typer.secho("  all checks passed", fg=typer.colors.GREEN)

    # Not findings, and deliberately not gating the exit code: these are things
    # no check can judge. Consent wording is the clear case - every check above
    # can pass on wording that is misleading, and by the time it is wrong
    # somebody has already agreed to something.
    for note in review_notes(spec):
        typer.echo("")
        typer.secho("  [review] ", fg=typer.colors.CYAN, nl=False)
        typer.echo(note)

    if problems:
        raise typer.Exit(code=1)


@survey_app.command("rows")
def survey_rows(
    source: Annotated[Path, typer.Argument(help="The spec to show: .json or .xlsx.")],
    lang: Annotated[
        str | None, typer.Option("--lang", help="Which language's text to show.")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Print the instrument as rows, to read it without opening Excel.

    A Studio canvas cannot be read in a terminal and a workbook cannot be read
    over ssh. This is the whole instrument in one screen, which is the thing the
    spec exists to make possible.
    """
    configure(verbose)

    try:
        spec = _load_any_spec(source)
    except SpecError as exc:
        _fail(str(exc))

    language = lang or spec.settings.default_language
    if language not in spec.settings.languages:
        _fail(
            f"{language!r} is not one of this spec's languages "
            f"({', '.join(spec.settings.languages)})."
        )

    indent = 0
    for row in spec.survey:
        if row.type == "end group":
            indent = max(0, indent - 1)
            continue

        widgets = spec.widget_count(row)
        text = (row.label.get(language) or "").replace("\n", " ").strip()
        if row.sends_template:
            text = f"[template: {row.template.get(language, '?')}]"

        prefix = "  " * indent
        typer.echo(f"  {widgets:>2}w  {prefix}", nl=False)
        typer.secho(f"{row.type} ", fg=typer.colors.CYAN, nl=False)
        typer.secho(f"{row.name}", bold=True, nl=False)
        if row.relevance:
            typer.secho(f"  when {row.relevance}", fg=typer.colors.YELLOW, nl=False)
        typer.echo("")

        if text:
            typer.echo(f"        {prefix}{text[:90]}")
        for choice in spec.choice_list(row.list_name) if row.list_name else []:
            label = choice.label.get(language, "")
            typer.echo(
                f"        {prefix}  {choice.value:>4} = {label}  "
                f"({choice.resolved_id()})"
            )

        if row.type == "begin group":
            indent += 1

    typer.echo("")
    typer.secho(
        f"  {spec.total_widget_count()} widgets, from {len(spec.survey)} rows",
        fg=typer.colors.GREEN,
    )


@survey_app.command("template")
def survey_template(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Where to write the workbook."),
    ] = Path("survey.xlsx"),
    lang: Annotated[
        str, typer.Option("--lang", help="Language code for the example text.")
    ] = "en",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Write a documented starter workbook to fill in.

    Not an empty sheet. The first question anybody has about this format is what
    a filled-in row looks like, and a header row with nothing under it cannot
    answer that - so the template is a small, working three-question instrument
    showing one row of every type, which `rtt survey check` passes as-is. Delete
    what you do not need and edit the rest.

    That matters for a reason beyond convenience: given a blank sheet, the next
    move is to find an existing survey and copy it, and copying an existing
    survey is how a family of flows comes to share one identical
    break-off defect.
    """
    configure(verbose)

    if output.exists():
        typer.secho(
            f"{output} already exists. Move it aside first - this would "
            f"overwrite an instrument somebody may have filled in.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    spec = starter_spec(lang)
    try:
        written = write_xlsx(spec, output)
    except SpecError as exc:
        _fail(str(exc))

    typer.secho(f"Wrote {written}", fg=typer.colors.GREEN, bold=True)
    typer.echo(
        f"  {len(spec.survey)} rows, {len(spec.questions())} questions, "
        f"{len(spec.choices)} options -> {spec.total_widget_count()} widgets"
    )
    typer.echo(
        "\n  Start with the help-survey sheet: it documents every column, and it "
        "\n  travels inside the file so it is there when you need it."
    )

    typer.echo("")
    typer.secho("  SCOPE", fg=typer.colors.YELLOW, bold=True)
    for line in SCOPE_NOTE.split("\n"):
        typer.echo(f"  {line}" if line else "")

    typer.echo("")
    typer.echo("  Next:")
    typer.echo(f"    rtt survey check {written}")
    typer.echo(
        f"    rtt survey convert {written} {written.with_suffix('.json')}"
        "   # then commit the JSON"
    )
