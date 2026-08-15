"""Write to a Google Sheet from this machine, with the service account.

The counterpart to ``twilio_functions/publish_gsheets.js``. That one runs inside
a Twilio Function and appends one row per submission as the flow reaches its
publish widget; this one runs locally and rewrites a whole tab, which is what
delivery status needs - a respondent's state changes from ``sent`` to
``delivered`` to ``answered_back``, so appending would write the same person
repeatedly and leave the reader to work out which line is current.

Between them a workbook can hold a live round:

    data tab       written by Twilio, one row per submission, append-only
    tracking tab   written from here every poll, one row per respondent

**Neither tab ever holds a phone number.** Both are keyed on caseid. An
unencrypted number exists in exactly two places in this project - the master
list a round is drawn from, and the dataset after `rtt decrypt` - and a shared
spreadsheet is emphatically neither.

No Google client library. Authenticating a service account is a signed JWT
exchanged for an access token, which is a few lines against `cryptography` and
`requests` - both already dependencies - where `google-api-python-client` would
add a large tree for one POST and two PUTs. It also keeps this file honest about
what the JS does, since the two now perform visibly the same exchange.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import pandas as pd
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import config as cfg
from .log import get_logger

logger = get_logger()

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

#: Google caps an assertion's lifetime at one hour. Asking for less costs
#: nothing - a poll takes seconds - and shortens the window in which a token
#: intercepted from this machine is useful.
TOKEN_LIFETIME_SECONDS = 600

TIMEOUT = 30


class SheetsError(Exception):
    """Raised when the sheet cannot be authenticated to, read or written."""


def _b64(raw: bytes) -> str:
    """Base64url without padding, which is what a JWT uses."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def credentials_from_env() -> tuple[str, str, str]:
    """Return ``(client_email, private_key, sheet_id)`` from the environment.

    Raises:
        SheetsError: If any of the three is missing.

    ``GOOGLE_JWT_TOKEN`` is a misnomer inherited from the Twilio Console and
    kept because every `.env` in circulation already uses it. The value is not a
    JWT: it is the RSA private key that *signs* one. ``GOOGLE_PRIVATE_KEY`` and
    ``GOOGLE_CLIENT_EMAIL`` are accepted as the clearer aliases.

    """
    cfg.load_env()
    client_email = cfg.optional("GOOGLE_EMAIL") or cfg.optional("GOOGLE_CLIENT_EMAIL")
    private_key = cfg.optional("GOOGLE_JWT_TOKEN") or cfg.optional("GOOGLE_PRIVATE_KEY")
    sheet_id = cfg.optional("GOOGLE_SHEET_ID")

    missing = [
        name
        for name, value in (
            ("GOOGLE_EMAIL", client_email),
            ("GOOGLE_JWT_TOKEN", private_key),
            ("GOOGLE_SHEET_ID", sheet_id),
        )
        if not value
    ]
    if missing:
        raise SheetsError(
            f"{', '.join(missing)} not set in .env. The same three the Twilio "
            f"publish Function uses - see docs/setup.md."
        )
    return client_email, private_key, sheet_id


def access_token(client_email: str, private_key: str) -> str:
    """Exchange a signed assertion for a Google access token.

    Args:
        client_email: The service account's address.
        private_key: Its PEM private key, with real newlines or escaped ones.

    Returns:
        A bearer token valid for :data:`TOKEN_LIFETIME_SECONDS`.

    Raises:
        SheetsError: If the key will not load or Google refuses the assertion.

    """
    # A key that came via a Twilio-shaped variable carries literal "\n"; one
    # read from a file or a multi-line .env value carries real newlines. Both
    # are common and only one of them parses.
    pem = private_key.replace("\\n", "\n").encode()

    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception as exc:
        raise SheetsError(
            f"GOOGLE_JWT_TOKEN is not a usable PEM private key ({exc}). Despite "
            f"the name it is not a JWT and not an access token - those expire "
            f"within the hour and cannot be used to mint new ones."
        ) from exc

    issued = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(
        json.dumps(
            {
                "iss": client_email,
                "scope": SCOPE,
                "aud": TOKEN_URL,
                "iat": issued,
                "exp": issued + TOKEN_LIFETIME_SECONDS,
            }
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    signature = _b64(key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256()))
    assertion = f"{header}.{claims}.{signature}"

    try:
        response = requests.post(
            TOKEN_URL,
            data={"grant_type": GRANT_TYPE, "assertion": assertion},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SheetsError(f"Could not reach Google to authenticate: {exc}") from exc

    if response.status_code >= 300:
        # Google's error body names the cause - clock skew, a revoked key, an
        # API that was never enabled - and it is not a secret.
        raise SheetsError(
            f"Google refused the service account (HTTP {response.status_code}): "
            f"{response.text[:300]}"
        )

    token = response.json().get("access_token")
    if not token:
        raise SheetsError("Google returned no access_token.")
    return token


def quote_tab(tab: str) -> str:
    """Quote a tab name for A1 notation.

    Single quotes are the escape, and a literal quote inside the name doubles,
    so a tab called ``Dan's round`` becomes ``'Dan''s round'``. Without this a
    tab whose name contains a space is a 400 that reads like a range error.
    """
    return "'" + str(tab).replace("'", "''") + "'"


def _request(method: str, url: str, token: str, **kwargs: Any) -> dict:
    """Make one Sheets API call and return its JSON."""
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise SheetsError(f"Google Sheets request failed: {exc}") from exc

    if response.status_code >= 300:
        detail = response.text[:300]
        if response.status_code == 403:
            detail += (
                "\n\nMost often this means the sheet is not shared with the "
                "service account. Share it with GOOGLE_EMAIL as an Editor - "
                "creating a service account grants it nothing by itself."
            )
        if response.status_code == 400 and "Unable to parse range" in detail:
            detail += (
                "\n\nThe tab does not exist. Create it in the workbook first; "
                "this writes to a tab, it does not add one."
            )
        raise SheetsError(
            f"Google Sheets returned HTTP {response.status_code}: {detail}"
        )

    return response.json() if response.content else {}


def replace_tab(
    frame: pd.DataFrame, *, sheet_id: str, tab: str, token: str
) -> tuple[int, int]:
    """Rewrite a whole tab with a frame: header row, then every row.

    Args:
        frame: What the tab should contain.
        sheet_id: The spreadsheet's ID.
        tab: The tab (worksheet) name. It must already exist.
        token: A bearer token from :func:`access_token`.

    Returns:
        ``(rows, columns)`` written, excluding the header.

    Raises:
        SheetsError: If the frame holds a column that looks like a phone number,
            or the API call fails.

    Clear-then-write rather than update-in-place. A respondent's delivery state
    moves and rows are re-sorted between polls, so writing over the old values
    without clearing would leave the tail of a longer previous poll stranded
    below the new data, where it reads as live rows that stopped updating.

    """
    _refuse_phone_numbers(frame)

    target = f"{quote_tab(tab)}!A1"
    _request(
        "POST",
        f"{SHEETS_API}/{sheet_id}/values/{quote_tab(tab)}:clear",
        token,
    )

    values = [list(frame.columns)] + frame.astype(str).values.tolist()
    _request(
        "PUT",
        f"{SHEETS_API}/{sheet_id}/values/{target}",
        token,
        params={"valueInputOption": "RAW"},
        json={"values": values},
    )
    logger.debug("wrote %d row(s) to tab %s", len(frame), tab)
    return len(frame), len(frame.columns)


#: Column names that would mean a phone number reached the sheet. Checked by
#: name rather than by inspecting values: a column called `number` holding
#: something else is still a naming mistake worth stopping, and scanning every
#: cell of every poll for phone-shaped strings would be both slower and easier
#: to fool.
_FORBIDDEN_COLUMNS = frozenset({"number", "phone", "to", "from_", "from", "contact"})


def _refuse_phone_numbers(frame: pd.DataFrame) -> None:
    """Stop a frame carrying respondent numbers from reaching a shared sheet.

    The invariant this module exists to keep, enforced at the last point before
    the data leaves the machine. A published sheet is shared with people who
    have no reason to hold identifiers, and once a row is written it has been
    disclosed - deleting it afterwards does not undo that.
    """
    offending = sorted(
        column
        for column in frame.columns
        if str(column).strip().lower() in _FORBIDDEN_COLUMNS
    )
    if offending:
        raise SheetsError(
            f"Refusing to write column(s) {', '.join(offending)} to a shared "
            f"spreadsheet: they hold respondent phone numbers. Everything "
            f"published is keyed on caseid instead. If this column is genuinely "
            f"not a phone number, rename it."
        )
