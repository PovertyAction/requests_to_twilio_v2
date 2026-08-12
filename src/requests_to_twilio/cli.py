"""Command-line interface.

Credentials are never accepted as flags. Everything secret comes from the
environment or a ``.env`` file, because arguments on a command line land in
shell history and are readable by any other process on the machine.
"""

from __future__ import annotations

import contextlib
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
from .launcher import LaunchError, launch
from .log import configure
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


def main() -> None:
    """Entry point used by the ``rtt`` script."""
    try:
        app()
    except KeyboardInterrupt:
        typer.secho("\nInterrupted.", fg=typer.colors.YELLOW, err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
