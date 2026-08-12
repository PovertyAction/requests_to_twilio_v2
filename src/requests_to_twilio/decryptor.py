"""Decrypt the encrypted columns of a dataset downloaded from Google Sheets.

The pre-2.0 version refused to run unless the file sat on an ``X:`` drive, which
was Boxcryptor's mount point. Boxcryptor has since been discontinued, so that
check protected nothing and merely blocked legitimate use. There is no path
restriction now; the tool warns about what it is writing and leaves storage
policy to the operator.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .crypto import V2_PREFIX, CryptoError, decrypt
from .log import get_logger

#: Written in place of a value that could not be decrypted, so that one bad cell
#: does not abort a 5,000-row file and the failures stay visible in the output.
FAILURE_MARKER = "<DECRYPTION FAILED>"

logger = get_logger()


class DecryptionError(Exception):
    """Raised when a dataset cannot be processed at all."""


def find_encrypted_columns(frame: pd.DataFrame) -> list[str]:
    """Detect which columns hold v2 ciphertext.

    Args:
        frame: The loaded dataset.

    Returns:
        Names of columns where at least one value carries the ``v2:`` marker.
        Auto-detection exists because naming columns by hand is the step people
        get wrong, and a missed column means PII silently stays encrypted while
        the run reports success.

    """
    encrypted = []
    for column in frame.columns:
        values = frame[column].dropna().astype(str)
        if values.str.startswith(V2_PREFIX).any():
            encrypted.append(str(column))
    return encrypted


def decrypt_dataset(
    *,
    input_path: Path,
    private_key: X25519PrivateKey | None,
    columns: list[str] | None = None,
    legacy_secret: str | None = None,
    output_path: Path | None = None,
) -> tuple[Path, int, int]:
    """Decrypt the encrypted columns of a CSV or Excel file.

    Args:
        input_path: The downloaded dataset.
        private_key: The researcher's private key, for v2 data.
        columns: Columns to decrypt. When omitted, v2 columns are auto-detected;
            legacy data has no marker, so it must be named explicitly.
        legacy_secret: The pre-2.0 secret, for data collected before version 2.
        output_path: Where to write. Defaults to ``<input>_decrypted.csv``.

    Returns:
        A tuple of (output path, values decrypted, values that failed).

    Raises:
        DecryptionError: If the file cannot be read or no columns can be found.

    """
    if not input_path.is_file():
        raise DecryptionError(f"File not found: {input_path}")

    suffix = input_path.suffix.lower()
    try:
        if suffix in {".xlsx", ".xlsm"}:
            frame = pd.read_excel(input_path, dtype=str)
        elif suffix == ".csv":
            frame = pd.read_csv(input_path, dtype=str)
        else:
            raise DecryptionError(
                f"Unsupported format {suffix!r}; download the sheet as .csv."
            )
    except DecryptionError:
        raise
    except Exception as exc:
        raise DecryptionError(f"Could not read {input_path}: {exc}") from exc

    if columns:
        missing = [c for c in columns if c not in frame.columns]
        if missing:
            raise DecryptionError(
                f"Column(s) not in file: {', '.join(missing)}. "
                f"Available: {', '.join(map(str, frame.columns))}"
            )
        targets = columns
    else:
        targets = find_encrypted_columns(frame)
        if not targets:
            raise DecryptionError(
                "No encrypted columns found. If this is data from before "
                "version 2.0 it carries no marker, so name the columns with "
                "--columns and pass --legacy-secret."
            )
        logger.info("Auto-detected encrypted column(s): %s", ", ".join(targets))

    decrypted_count = 0
    failed_count = 0
    reported: set[str] = set()

    for column in targets:
        results = []
        for value in frame[column]:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                results.append(value)
                continue

            text = str(value)
            if not text or text == "nan":
                results.append("")
                continue

            try:
                results.append(decrypt(text, private_key, legacy_secret=legacy_secret))
                decrypted_count += 1
            except CryptoError as exc:
                results.append(FAILURE_MARKER)
                failed_count += 1
                # Report each distinct reason once; a wrong key produces one
                # identical failure per row and would otherwise flood the log.
                reason = str(exc).split(".")[0]
                if reason not in reported:
                    reported.add(reason)
                    logger.warning("Column %r: %s", column, exc)

        frame[column] = results

    destination = output_path or input_path.with_name(
        f"{input_path.stem}_decrypted.csv"
    )
    frame.to_csv(destination, index=False)

    logger.info(
        "Decrypted %d value(s) across %d column(s); %d failed",
        decrypted_count,
        len(targets),
        failed_count,
    )
    logger.warning(
        "%s now contains plain-text PII. Store it per IPA policy - inside a "
        "Cryptomator vault or an access-controlled Box folder - and do not "
        "commit it or email it.",
        destination,
    )

    return destination, decrypted_count, failed_count
