"""Logging configured so that respondent data does not leak into terminals,
scrollback, or CI output.

The pre-2.0 launcher printed the whole request payload for every respondent -
phone number, name, city, case ID - straight to stdout. That output routinely
ended up pasted into chat messages and support tickets. Here, phone numbers are
masked by a filter applied to every record, so a stray f-string cannot undo it.
"""

from __future__ import annotations

import logging
import re

#: Matches a run of digits long enough to be a phone number, with the separators
#: people commonly write them with. Deliberately broad: over-masking a number in
#: a log line costs nothing, under-masking leaks PII.
_PHONE_RE = re.compile(r"(\+?\d[\d\s\-().]{6,}\d)")

LOGGER_NAME = "rtt"


def mask_phone(number: str) -> str:
    """Mask a phone number for display, keeping only the last four digits.

    Args:
        number: The number in any format, e.g. ``whatsapp:+57 300 1234567``.

    Returns:
        A masked form such as ``whatsapp:+**********4567``, which is enough to
        match a row against a source file without exposing the number itself.

    """
    prefix = ""
    value = number
    if ":" in value:
        prefix, _, value = value.partition(":")
        prefix += ":"

    digits = re.sub(r"\D", "", value)
    if len(digits) <= 4:
        return f"{prefix}{'*' * len(digits)}"

    lead = "+" if value.strip().startswith("+") else ""
    return f"{prefix}{lead}{'*' * (len(digits) - 4)}{digits[-4:]}"


class PhoneRedactingFilter(logging.Filter):
    """Masks anything phone-shaped in a log record before it is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record's message in place, then allow it through."""
        message = record.getMessage()
        redacted = _PHONE_RE.sub(lambda m: mask_phone(m.group(1)), message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure(verbose: bool = False) -> logging.Logger:
    """Set up and return the package logger.

    Args:
        verbose: Emit DEBUG-level detail. Even in verbose mode the redaction
            filter stays on; there is no flag that prints raw phone numbers.

    Returns:
        The configured logger.

    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    # configure() may be called more than once in a test session.
    for existing in list(logger.handlers):
        logger.removeHandler(existing)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))
    handler.addFilter(PhoneRedactingFilter())
    logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    """Return the package logger without reconfiguring it."""
    return logging.getLogger(LOGGER_NAME)
