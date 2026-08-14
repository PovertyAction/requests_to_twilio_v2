# Setup reference

[START-HERE.md](../START-HERE.md) is the ordered walkthrough. This page is the
reference: every configuration value, where it comes from, and what breaks
without it.

## `.env`

Copy `.env.example` and fill it in. `.env` is gitignored. Nothing secret is ever
passed as a command-line flag — arguments land in shell history and are readable
from the process table.

### Twilio

| Variable | Where it comes from | Needed for |
| --- | --- | --- |
| `TWILIO_ACCOUNT_SID` | Console → Account Info. Starts `AC` | everything |
| `TWILIO_AUTH_TOKEN` | Console → Account Info | everything |
| `TWILIO_NUMBER` | your sender, **with the `whatsapp:` prefix** | `launch` |
| `TWILIO_FLOW_ID` | the flow's URL in the Console. Starts `FW` | `launch`, `fetch`; overridable with `--flow-id` |

The auth token is a live credential for your whole account: it can send messages
from your number to anyone. Treat it like a password.

`TWILIO_NUMBER` without the `whatsapp:` prefix is an SMS address. The tooling
warns rather than blocking, because an SMS round is a legitimate thing to run —
but the inbound-routing check will then be describing SMS, not WhatsApp.

### Encryption

| Variable | |
| --- | --- |
| `ENCRYPTION_PRIVATE_KEY` | the key inline, or leave blank and use the file below |
| `ENCRYPTION_PRIVATE_KEY_FILE` | path to what `just keygen` wrote. Default `rtt_private_key.txt` |
| `LEGACY_SECRET_KEY` | only for data collected before 2.0. Leave blank otherwise |

The **public** key is deliberately absent: this machine never needs it.
`just deploy-functions` derives it from the private key and sets it on the
Function, which also avoids the failure mode where someone pastes the *private*
key into the public slot — that succeeds silently and produces permanently
undecryptable data.

Set `LEGACY_SECRET_KEY` only when you actually need it. It is not dangerous, but
it makes `rtt decrypt` attempt v1 decryption on unmarked values in every column
it touches.

### Warehouse

| Variable | Used by | |
| --- | --- | --- |
| `MOTHERDUCK_TOKEN` | `rtt push`, the publish Function | account-scoped, so it grants more than one database |
| `MOTHERDUCK_DATABASE` | `rtt push` **and** the publish Function | `just deploy-functions` fails without it |
| `MOTHERDUCK_HOST` | the publish Function only | e.g. `pg.us-east-1-aws.motherduck.com` |
| `MOTHERDUCK_TABLE` | the publish Function only | fully qualified: `db.schema.table` |

The Function reaches MotherDuck over the **Postgres wire protocol**, which needs
a host; the Python side connects with `md:` and does not. MotherDuck has no REST
API for `INSERT`, and `node-postgres` is pure JavaScript so it loads in a Twilio
Function — the DuckDB driver would not, since it needs a native binary.

A MotherDuck token is **account-scoped, not database-scoped**, so the token in
your Twilio Console grants access to every database on the account. That is a
reason to use a dedicated service user for a real round, and a reason encryption
still matters even though the warehouse is yours: it separates *can write* from
*can read identifiers*.

### Twilio API key — only for the MCP servers

| Variable | |
| --- | --- |
| `TWILIO_API_KEY` | Console → Account → API keys & tokens → Create API key (Standard). Starts `SK` |
| `TWILIO_API_SECRET` | shown exactly once, at creation |

`rtt` itself does not use these. They exist for the Twilio MCP server, and they
are **a different credential from the auth token** — the most common reason
`just mcp-list` reports "not ready".

Prefer an API key over the auth token wherever a choice exists: it can be revoked
on its own without rotating the credential for your whole account.

## Claude Code and the MCP servers

`.mcp.json` defines two, and neither is required to run a survey.

**`twilio-docs`** — HTTP, **no credentials, no setup**. Already working. It is
the right way to answer a Twilio question rather than guessing at a widget schema
or an error code.

**`twilio`** — `@twilio-alpha/mcp`, pinned, launched with `npx`, so it needs
Node. It can read and modify Studio flows and Content templates. Setup:

```powershell
# 1. put TWILIO_API_KEY and TWILIO_API_SECRET in .env
just mcp-setup      # 2. writes .claude/settings.local.json (gitignored)
# 3. restart Claude Code
just mcp-list       # 4. confirm
```

`mcp-setup` refuses to write if git would track the target file, because that is
how credentials reach a public repository.

Two cautions. It is an **alpha** package, pinned here for that reason. And it
exposes the Meta template-submission endpoint, which is **irreversible** and
bypasses the confirmation `rtt template submit` asks for — prefer the CLI for
anything that writes.

## Toolchain

```powershell
just pre-install    # uv, gh, Node, markdownlint
just get-started    # uv sync + pre-commit hooks
```

Node is not optional even if you never touch JavaScript: the encryption test
suite runs the real Function code, and the MCP server is launched with `npx`.

`uv sync` reads `pyproject.toml` and `uv.lock`, so everyone gets byte-identical
dependency versions. There is no `pip install`, no `requirements.txt`, and no
Visual C++ build-tools step.

## What is gitignored, and why it matters

`.env`, `rtt_private_key.txt`, `.claude/settings.local.json`, all `*.csv` and
`*.xlsx` except the committed `sample_input.xlsx` and `codebook/*.csv`, and
`flows/` except the tracked reference definitions.

Pre-commit runs `detect-private-key`; gitleaks runs in CI, because IPA-managed
Windows blocks the gitleaks binary under Application Control. None of this is
decorative — an earlier version of this repository shipped a live Google
service-account key in its source. See [SECURITY.md](../SECURITY.md).
