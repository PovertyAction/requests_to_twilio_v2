"""Load collected datasets into MotherDuck, IPA's analysis warehouse.

This is the last hop of the pipeline: once responses are decrypted, pushing them
here removes the manual upload that otherwise sits between collection and
analysis, and gives everyone a single table to query instead of a folder of
CSVs at various ages.

**Data classification.** Anything pushed after ``rtt decrypt`` is plain-text PII
and therefore Confidential under IPA's policy. Send it to a database that is
approved for that classification and access-controlled accordingly - not to a
shared scratch database. When in doubt, push the *pseudonymised* columns and
leave direct identifiers out; :func:`push_dataframe` accepts a column subset for
exactly that reason.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from .config import optional, require
from .log import get_logger

ENV_TOKEN = "MOTHERDUCK_TOKEN"  # noqa: S105 - a variable name, not a secret
ENV_DATABASE = "MOTHERDUCK_DATABASE"

#: Set in `.env` for the publish Function, which reaches MotherDuck over the
#: Postgres wire protocol from inside Twilio and needs the `pg.` endpoint. The
#: DuckDB extension reads the same variable name and means something else by it:
#: the host it fetches extension metadata from. The Postgres endpoint does not
#: serve that, so leaving this set makes every local connection fail with
#: "Failed to download .../extension_version" - a message that names neither
#: MotherDuck nor the variable that caused it.
#:
#: One name, two protocols, one `.env`. The Function still gets its value; this
#: module just declines to read the Function's configuration as its own.
ENV_FUNCTION_HOST = "MOTHERDUCK_HOST"

#: How an existing table is treated.
WRITE_MODES = ("replace", "append", "create")

logger = get_logger()


class WarehouseError(Exception):
    """Raised when the warehouse cannot be reached or written to."""


def _connect(database: str):
    """Open a MotherDuck connection.

    Args:
        database: The MotherDuck database name.

    Returns:
        A live DuckDB connection attached to MotherDuck.

    Raises:
        WarehouseError: If the token is missing or the connection is refused.

    """
    import duckdb

    token = require(ENV_TOKEN)

    # Hidden from the extension for the length of the connect call only - see
    # ENV_FUNCTION_HOST. Restored afterwards because `rtt deploy-functions`
    # reads it from this same process to configure the Function.
    stashed = os.environ.pop(ENV_FUNCTION_HOST, None)
    try:
        # The token is passed via the connection config rather than the DSN so
        # it does not end up in a connection string that might get logged.
        return duckdb.connect(f"md:{database}", config={"motherduck_token": token})
    except Exception as exc:
        raise WarehouseError(
            f"Could not connect to MotherDuck database {database!r}: {exc}"
        ) from exc
    finally:
        if stashed is not None:
            os.environ[ENV_FUNCTION_HOST] = stashed


def resolve_database(override: str | None) -> str:
    """Pick the target database, preferring an explicit override."""
    value = override or optional(ENV_DATABASE)
    if not value:
        raise WarehouseError(
            f"No MotherDuck database given. Pass --database or set {ENV_DATABASE}."
        )
    return value


def push_dataframe(
    *,
    frame: pd.DataFrame,
    table: str,
    database: str,
    mode: str = "append",
    columns: list[str] | None = None,
) -> int:
    """Write a dataframe to a MotherDuck table.

    Args:
        frame: The data to load.
        table: Target table name, optionally schema-qualified.
        database: Target MotherDuck database.
        mode: ``append`` adds rows and is the default, because the table a flow
            publishes to is the database of record for a round. ``replace``
            issues ``CREATE OR REPLACE TABLE`` and therefore destroys it;
            ``create`` fails if the table already exists.
        columns: Restrict to these columns. Use this to leave direct identifiers
            out of the warehouse.

    Returns:
        The number of rows written.

    Raises:
        WarehouseError: On a bad mode, unknown column, or write failure.

    """
    if mode not in WRITE_MODES:
        raise WarehouseError(
            f"mode must be one of {', '.join(WRITE_MODES)}, got {mode!r}"
        )

    payload = frame
    if columns:
        missing = [c for c in columns if c not in frame.columns]
        if missing:
            raise WarehouseError(f"Column(s) not in dataset: {', '.join(missing)}")
        payload = frame[columns]

    if payload.empty:
        logger.warning("Nothing to push: the dataset has no rows.")
        return 0

    connection = _connect(database)
    try:
        # Registering the frame lets DuckDB read it directly, so the data is
        # never serialised through a SQL string.
        connection.register("_rtt_payload", payload)

        if mode == "replace":
            statement = (
                f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM _rtt_payload'  # noqa: S608
            )
        elif mode == "create":
            statement = f'CREATE TABLE "{table}" AS SELECT * FROM _rtt_payload'  # noqa: S608
        else:
            connection.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" AS SELECT * FROM _rtt_payload LIMIT 0'  # noqa: S608
            )
            # BY NAME, not positional. A plain INSERT ... SELECT * matches by
            # position, so a dataset whose columns are ordered differently from
            # the existing table writes every value into its neighbour's column.
            # Everything published by the flow is VARCHAR, so there is no type
            # error to catch it - the data is simply wrong.
            statement = f'INSERT INTO "{table}" BY NAME SELECT * FROM _rtt_payload'  # noqa: S608

        connection.execute(statement)
    except Exception as exc:
        raise WarehouseError(f"Write to {database}.{table} failed: {exc}") from exc
    finally:
        connection.close()

    logger.info(
        "Pushed %d row(s), %d column(s) to %s.%s (%s)",
        len(payload),
        len(payload.columns),
        database,
        table,
        mode,
    )
    return len(payload)


def push_file(
    *,
    path: Path,
    table: str,
    database: str,
    mode: str = "append",
    columns: list[str] | None = None,
) -> int:
    """Read a CSV or Excel file and push it to MotherDuck.

    Args:
        path: The dataset to load.
        table: Target table name.
        database: Target MotherDuck database.
        mode: One of :data:`WRITE_MODES`.
        columns: Restrict to these columns.

    Returns:
        The number of rows written.

    Raises:
        WarehouseError: If the file cannot be read.

    """
    if not path.is_file():
        raise WarehouseError(f"File not found: {path}")

    try:
        # dtype=str throughout, matching every other reader in the package. A
        # caseid like `007` is an identifier, not a number, and inferring types
        # here would push `7` into the warehouse and break the join back to the
        # sampling frame.
        if path.suffix.lower() in {".xlsx", ".xlsm"}:
            frame = pd.read_excel(path, dtype=str)
        else:
            frame = pd.read_csv(path, dtype=str)
    except Exception as exc:
        raise WarehouseError(f"Could not read {path}: {exc}") from exc

    return push_dataframe(
        frame=frame, table=table, database=database, mode=mode, columns=columns
    )
