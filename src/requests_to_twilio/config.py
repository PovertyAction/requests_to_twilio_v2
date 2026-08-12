"""Configuration loaded from the environment.

Credentials are read from environment variables or a local ``.env`` file, never
from command-line flags. Arguments passed on a command line end up in shell
history and are visible to every other process on the machine via the process
table, which is not an acceptable home for a Twilio auth token or an encryption
key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, find_dotenv, load_dotenv

ENV_ACCOUNT_SID = "TWILIO_ACCOUNT_SID"
ENV_AUTH_TOKEN = "TWILIO_AUTH_TOKEN"  # noqa: S105 - a variable name, not a secret
ENV_FROM_NUMBER = "TWILIO_NUMBER"
ENV_FLOW_ID = "TWILIO_FLOW_ID"
ENV_ENCRYPTION_KEY = "ENCRYPTION_KEY"
ENV_LEGACY_SECRET = "LEGACY_SECRET_KEY"  # noqa: S105 - a variable name, not a secret


class ConfigError(Exception):
    """Raised when required configuration is missing or malformed."""


def load_env(env_file: Path | None = None) -> None:
    """Load a ``.env`` file into the process environment.

    Args:
        env_file: Explicit path to an env file. When omitted, python-dotenv
            searches upward from the working directory for a ``.env``.

    Existing environment variables always win, so a value exported in the shell
    or injected by CI is never silently overridden by a stale local file.

    """
    if env_file is not None:
        if not env_file.is_file():
            raise ConfigError(f"Env file not found: {env_file}")
        load_dotenv(env_file, override=False)
        return

    # usecwd=True searches upward from the working directory. Without it,
    # python-dotenv searches upward from *this module's* file instead, which
    # sits in site-packages for any non-editable install - so the project's
    # .env is silently never found and every credential looks unset.
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)
        _fill_blanks(found)


def _fill_blanks(env_file: str) -> None:
    """Let `.env` win over variables that are present but empty.

    ``override=False`` keeps a real exported value from being clobbered, which
    is what we want. But it also treats an empty string as a deliberate
    override, so a blank placeholder - from a CI variable, an editor's env
    block, or a tool that exports every key it knows about - silently beats a
    correct value in `.env`, and every credential reads as unset.

    An empty variable is never a meaningful override, so treat it as absent.
    """
    for name, value in dotenv_values(env_file).items():
        if value and not os.environ.get(name, "").strip():
            os.environ[name] = value


def require(name: str) -> str:
    """Read a required environment variable.

    Args:
        name: The variable's name.

    Returns:
        Its value, stripped of surrounding whitespace.

    Raises:
        ConfigError: If it is unset or empty.

    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Add it to your .env file "
            f"(copy .env.example to .env), or export it in your shell."
        )
    return value


def optional(name: str) -> str | None:
    """Read an optional environment variable, returning None when unset."""
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class TwilioConfig:
    """Credentials and defaults for talking to Twilio."""

    account_sid: str
    auth_token: str
    from_number: str | None = None
    flow_id: str | None = None

    @classmethod
    def from_env(cls) -> TwilioConfig:
        """Build a config from the environment.

        Returns:
            A populated config. ``from_number`` and ``flow_id`` are optional
            here because they can also be supplied per command, where naming the
            flow explicitly is often clearer than relying on a default.

        """
        return cls(
            account_sid=require(ENV_ACCOUNT_SID),
            auth_token=require(ENV_AUTH_TOKEN),
            from_number=optional(ENV_FROM_NUMBER),
            flow_id=optional(ENV_FLOW_ID),
        )

    def resolve_from_number(self, override: str | None) -> str:
        """Pick the sending number, preferring an explicit override."""
        value = override or self.from_number
        if not value:
            raise ConfigError(
                f"No sending number given. Pass --from-number or set {ENV_FROM_NUMBER}."
            )
        return value

    def resolve_flow_id(self, override: str | None) -> str:
        """Pick the Studio flow, preferring an explicit override."""
        value = override or self.flow_id
        if not value:
            raise ConfigError(f"No flow given. Pass --flow-id or set {ENV_FLOW_ID}.")
        if not value.startswith("FW"):
            raise ConfigError(
                f"Flow ID should start with 'FW', got {value!r}. "
                "Copy it from the Studio flow's URL in the Twilio Console."
            )
        return value
