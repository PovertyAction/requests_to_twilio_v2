# Contributing

This is IPA tooling for running WhatsApp and SMS surveys through Twilio Studio.
Contributions from other country offices and teams are welcome — the point of
publishing it is that the next round should not be rebuilt from scratch.

## Getting set up

```powershell
just get-started    # uv sync, pre-commit hooks, toolchain
cp .env.example .env
```

`just --list` is the map. `START-HERE.md` walks the whole path from a fresh
Twilio account to a launched round.

## Before you open a PR

```powershell
just test        # the suite must be green
just fmt-all     # ruff + markdownlint
just flow-check  # if you touched a flow or the builder
```

CI runs the test suite on Ubuntu and Windows, the lint hooks on Ubuntu, and a
gitleaks scan. `just flow-check` is not in CI and cannot be — checking a flow on
the account needs live Twilio credentials — so run it yourself, or point it at a
local definition file, which needs neither credentials nor network.

Node is required locally as well as in CI: the crypto test suite runs the
**real** JavaScript that executes inside Twilio and decrypts its output in
Python.

## The one rule worth internalising

**Check for the failures that report success.**

Every defect this project has shipped to production looked healthy at the moment
it failed. A publish widget returning 200 while dropping columns. Functions
deployed `private`, so every respondent completed the survey and nothing was
ever written. A flow whose reply webhook pointed at a different flow entirely,
while the launcher printed `5 sent, 0 failed`. A list tap returning an `id` where
the split expected a label, so every answer fell through to the retry nudge.

Failures that announce themselves need no tooling. So when you add a check, ask
what it would look like from the outside at the moment it fires — if the answer
is "an error", the check is probably not the one worth writing.

Two corollaries, both learned the hard way:

- **Offline confidence is not evidence.** Nine defects once survived a full test
  suite, a passing `flow-check` and a clean dry run. If a change touches the
  live path, test it against a real Twilio account before believing it.
- **A check that answers the wrong question is worse than no check**, because it
  is trusted. Two of ours reported green on a genuinely broken round. When you
  add a rule, confirm it *fails* against the broken input before you confirm it
  passes against the fixed one.

## Conventions

- **Comments explain why, not what.** The repo is read by researchers who will
  not know that Twilio caps an environment variable at 450 bytes, or that
  `matches_any_of` splits on commas inside an option label. Write those down.
- **Nothing secret on the command line.** Arguments are visible in shell history
  and to other processes. Credentials come from `.env`.
- **Guardrails over documentation** for anything irreversible. `rtt template
  submit` sends a template to Meta and it can never be edited afterwards, so the
  command confirms rather than trusting the operator to have read a warning.
- **Both halves of the encryption move together.** `crypto.py` and
  `encrypt_fields.js` must agree forever; a mismatch surfaces only as unreadable
  production data, after collection. `tests/test_interop.py` is what stops that.
- **Two implementations, one docs source.** If you fix a trap in the code, fix
  the document that taught it. We once corrected a builder bug during a live test
  and left the skill file handing the same defect to the next person.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not open a public issue for anything that
touches credentials or respondent data.
