# Security

## Reporting a vulnerability

Email <support@poverty-action.org>. Do not open a public issue.

If the report concerns data that may already be exposed — a credential in a
commit, a shared spreadsheet, a warehouse table — say so in the subject line, and
include what you can see rather than what you think it means. Everything else can
wait for a reply; that cannot.

## What this repository handles

Surveys run through this toolkit collect data from human research participants.
Two things follow from that.

**Credentials here reach real people.** A Twilio auth token can send messages
from your organisation's number to a sample of respondents. Treat a leaked token
as an incident with a human subject dimension, not just a billing one.

**The private key is not recoverable.** `rtt keygen` writes a key that is the
only thing able to read collected identifiers back. There is no reset and no
escrow, by design — the same custody model as SurveyCTO. Losing it destroys the
data; leaking it undoes the protection retroactively, for every round it ever
decrypted.

## What the encryption does and does not protect

Worth stating plainly, because the gap has surprised people:

- Identifiers are encrypted **inside Twilio, before publication**, with a public
  key. Twilio holds only the public half, so Console access does not grant access
  to identifiers in the published dataset. That is the protection, and it is real.
- Twilio **separately retains** the raw inbound message text, the respondent's
  number, and everything preloaded into the flow, in plain text, for roughly 30
  days. `rtt fetch` reads exactly that. Encryption cannot reach it — a messaging
  platform has to receive plaintext to deliver it.

So the controls that matter during collection are Twilio account access,
role scoping, and the retention window. Cryptography is what protects the copy
that outlives the round. See `docs/encryption.md` for the full threat model.

## Known-good practice for this repo

- Credentials live in `.env`, never in flags — arguments land in shell history
  and are readable from the process table.
- `.env`, `rtt_private_key.txt`, and all `*.csv` / `*.xlsx` are gitignored,
  except the committed example (`sample_input.xlsx`) and the value-label
  codebooks, which contain no respondent data.
  `just scan-secrets` catches private keys locally; gitleaks runs in CI.
- Decrypted output is plain-text PII the moment it is written. Store it per your
  organisation's policy for that classification.

## History

An earlier version of this repository committed a live Google service-account
key, which remained in public history for roughly 19 months. The credential has
been revoked. The pre-commit hooks, the CI secret scan, and the refusal in
`scripts/mcp_settings.py` to write credentials to a path git does not ignore all
exist because of it.
