"""Generate the Claude Code settings that the Twilio MCP server reads.

`.mcp.json` is committed and shared, so it references credentials as ``${VAR}``
rather than containing them. Those variables have to reach Claude Code's
environment somehow, and the place for that is `.claude/settings.local.json`,
which is gitignored.

This script derives that file from `.env`, so the credentials are written down
exactly once. Run it with ``just mcp-setup``.

Nothing is accepted on the command line: arguments land in shell history and are
readable from the process table by anything else running on the machine.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"
SETTINGS_FILE = REPO_ROOT / ".claude" / "settings.local.json"
MCP_FILE = REPO_ROOT / ".mcp.json"

#: Variables the MCP server's ${...} placeholders expand from.
REQUIRED = ("TWILIO_ACCOUNT_SID", "TWILIO_API_KEY", "TWILIO_API_SECRET")

#: Prefixes that identify a real value rather than a leftover placeholder.
EXPECTED_PREFIX = {"TWILIO_ACCOUNT_SID": "AC", "TWILIO_API_KEY": "SK"}


def fail(message: str) -> None:
    """Print an error and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def looks_like_placeholder(name: str, value: str) -> bool:
    """Detect a value still carrying the shape of .env.example's placeholder."""
    if not value:
        return True
    if "x" * 8 in value.lower():
        return True
    return value.startswith("PASTE_") or value.startswith("YOUR_")


def read_credentials() -> dict[str, str]:
    """Read and validate the Twilio credentials from `.env`."""
    if not ENV_FILE.is_file():
        fail(f"{ENV_FILE.name} not found. Copy .env.example to .env and fill it in.")

    values = dotenv_values(ENV_FILE)
    credentials: dict[str, str] = {}
    problems: list[str] = []

    for name in REQUIRED:
        value = (values.get(name) or "").strip()

        if looks_like_placeholder(name, value):
            problems.append(f"  {name} is empty or still a placeholder")
            continue

        prefix = EXPECTED_PREFIX.get(name)
        if prefix and not value.startswith(prefix):
            problems.append(
                f"  {name} should start with {prefix!r}, got {value[:4]!r}..."
            )
            continue

        credentials[name] = value

    if problems:
        fail(
            "Twilio API credentials are not ready in .env:\n"
            + "\n".join(problems)
            + "\n\nCreate a Standard API key at:\n"
            "  Twilio Console > Account > API keys & tokens > Create API key\n"
            "The secret is shown only once, at creation."
        )

    return credentials


def verify_ignored() -> None:
    """Refuse to write the settings file if git would track it.

    A misconfigured ignore rule is how credentials reach a public repository.
    This project has already shipped one live key that way, so the check runs
    before the write rather than after.
    """
    import subprocess

    relative = SETTINGS_FILE.relative_to(REPO_ROOT).as_posix()
    result = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-q", relative],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        fail(
            f"{relative} is NOT ignored by git. Refusing to write credentials to a "
            f"file that could be committed. Add it to .gitignore first."
        )


def merge_settings(credentials: dict[str, str]) -> dict:
    """Merge the credentials into any existing local settings.

    The file may already hold permissions or other local preferences, so only
    the `env` block is touched.
    """
    existing: dict = {}
    if SETTINGS_FILE.is_file():
        try:
            existing = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"{SETTINGS_FILE} is not valid JSON ({exc}). Fix or delete it.")

    env = dict(existing.get("env") or {})
    env.update(credentials)
    existing["env"] = env
    return existing


def list_servers() -> None:
    """Print the MCP servers this project defines and whether each is ready."""
    if not MCP_FILE.is_file():
        fail(f"{MCP_FILE.name} not found.")

    servers = json.loads(MCP_FILE.read_text(encoding="utf-8")).get("mcpServers", {})
    settings_env = {}
    if SETTINGS_FILE.is_file():
        # A malformed local settings file just means "not configured yet" here;
        # `mcp-setup` is where that gets reported properly.
        with contextlib.suppress(json.JSONDecodeError):
            settings_env = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get(
                "env", {}
            )

    for name, spec in servers.items():
        if spec.get("url"):
            target = spec["url"]
            ready = "no credentials needed"
        else:
            target = " ".join(spec.get("args", [])[:2])
            missing = [v for v in REQUIRED if not settings_env.get(v)]
            ready = (
                "ready"
                if not missing
                else f"NOT ready - run `just mcp-setup` ({len(missing)} var(s) unset)"
            )
        print(f"  {name:14} {target}")
        print(f"  {'':14} {ready}")


def main() -> None:
    """Write `.claude/settings.local.json` from the credentials in `.env`."""
    if "--list" in sys.argv[1:]:
        list_servers()
        return

    credentials = read_credentials()
    verify_ignored()

    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings = merge_settings(credentials)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    account = credentials["TWILIO_ACCOUNT_SID"]
    key = credentials["TWILIO_API_KEY"]

    print(f"Wrote {SETTINGS_FILE.relative_to(REPO_ROOT).as_posix()}")
    print(f"  TWILIO_ACCOUNT_SID  {account[:6]}...{account[-4:]}")
    print(f"  TWILIO_API_KEY      {key[:6]}...{key[-4:]}")
    print("  TWILIO_API_SECRET   (set, not shown)")
    print()
    print("Restart Claude Code for the MCP server to pick these up,")
    print("then run /mcp to confirm 'twilio' is connected.")


if __name__ == "__main__":
    main()
