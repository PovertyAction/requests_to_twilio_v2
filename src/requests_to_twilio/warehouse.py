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

from pathlib import Path

import pandas as pd

from .config import optional, require
from .log import get_logger

ENV_TOKEN = "MOTHERDUCK_TOKEN"  # noqa: S105 - a variable name, not a secret
ENV_DATABASE = "MOTHERDUCK_DATABASE"

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

    try:
        # The token is passed via the connection config rather than the DSN so
        # it does not end up in a connection string that might get logged.
        return duckdb.connect(f"md:{database}", config={"motherduck_token": token})
    except Exception as exc:
        raise WarehouseError(
            f"Could not connect to MotherDuck database {database!r}: {exc}"
        ) from exc


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
    mode: str = "replace",
    columns: list[str] | None = None,
) -> int:
    """Write a dataframe to a MotherDuck table.

    Args:
        frame: The data to load.
        table: Target table name, optionally schema-qualified.
        database: Target MotherDuck database.
        mode: ``replace`` overwrites, ``append`` adds rows, ``create`` fails if
            the table already exists.
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
            statement = f'INSERT INTO "{table}" SELECT * FROM _rtt_payload'  # noqa: S608

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
    mode: str = "replace",
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
        if path.suffix.lower() in {".xlsx", ".xlsm"}:
            frame = pd.read_excel(path)
        else:
            frame = pd.read_csv(path)
    except Exception as exc:
        raise WarehouseError(f"Could not read {path}: {exc}") from exc

    return push_dataframe(
        frame=frame, table=table, database=database, mode=mode, columns=columns
    )
