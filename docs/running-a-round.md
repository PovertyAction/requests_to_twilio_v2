# Running a round

Every command, and what each one refuses to do. `just <recipe>` wraps `rtt`;
`just --list` is the index, and `just cli` shows the CLI's own help.

Arguments pass through `just` unchanged, quoted or not. Quoting the whole
argument string — `just launch "sample.xlsx --dry-run"` — is a habit worth
keeping anyway, because it makes clear which flags belong to `rtt` rather than to
`just`, and `--dry-run` and `--verbose` exist on both.

## Once, before anything else

### `rtt keygen` — the encryption keypair

```powershell
just keygen
just keygen --out /secure/rtt_key.txt
just keygen --force        # overwrite an existing key file
```

Writes an X25519 private key and prints the matching public key. The public key
goes to Twilio (`just deploy-functions` handles it); the private key is the only
thing that can read collected identifiers back, and there is no reset.

`--force` exists because overwriting a key file silently would destroy access to
every round it had ever encrypted, so the default is to refuse.

## Before the round

### `rtt flow check` — high-frequency checks for the instrument

```powershell
just flow-check                                   # every flow on the account
just flow-check data_use_demo_en                  # one, by name or SID
just flow-check flows/data_use_demo_en.json       # or a local definition
just flow-check --errors-only
```

Exits non-zero on an error, so it works in CI. What it looks for, and why each
one is there:

| Finding | What it catches |
| --- | --- |
| `unpublished-paths` | a break-off that produces no row, so attrition is indistinguishable from never having been contacted |
| `publish-failure-thanks-respondent` | a failed publish that still sends the closing message — success from every angle, no data |
| `discarded-paths` | any other branch that ends without a row |
| `encrypt-failure-publishes-anyway` | blank identifier columns that look like missing data rather than a failure |
| `no-publish-widget-found` | the publish step was not recognised, so the checks above did not run at all |
| `unhandled-timeout` / `unhandled-delivery-failure` | a question with no handling for silence or an undelivered message |
| `no-optout-path` | nothing looks for a mid-survey "STOP", so it is stored as an answer and the next question is sent anyway |
| `trigger-ignores-api-launch` | the flow does not route `incomingRequest`, so an API launch ends at the trigger having sent nothing |
| `opening-not-a-template` | a free-form opener, which WhatsApp rejects with 63016 outside the 24-hour window |
| `opening-cannot-open-session` | a list picker as the opener — it cannot be approved, so it cannot open a conversation |
| `unmatchable-condition` | a split option no reply can ever reach. Evaluated, not read |
| `split-without-nomatch` | a split with no fallback, so an unexpected reply goes nowhere |
| `too-many-options` / `text-too-long` / `text-may-truncate` | past a Twilio limit; the create call fails with a generic error that names nothing |
| `respondent-initiated-start` | writing to the number starts the survey, so someone can begin a round nobody launched |
| `no-final-status` | publishes with no outcome variable, so completion cannot be told from break-off |
| `unpaired-answers` | an answer with no status beside it, so a blank cannot be read as timed-out vs not-asked |
| `no-encryption` | publishes with no encryption widget, so identifiers reach the warehouse in clear |
| `credentials` | a SID or token inside the definition |

### `rtt flow schema` — the table the flow expects

```powershell
just flow-schema flows/data_use_demo_en.json --table my_db.main.round1
```

Prints `CREATE TABLE` DDL derived from the publish widget's parameters. Run it
after every instrument change: the publish Function inserts only into columns
that already exist, and a new question with no column is **dropped behind an HTTP
200**.

### `rtt flow deploy` — ship it

```powershell
just flow-deploy flows/data_use_demo_en.json --publish
just flow-deploy flows/data_use_demo_en.json --name my_round_v2
just flow-deploy flows/data_use_demo_en.json --force     # ship despite findings
```

Runs the checks first and refuses on an error. Without `--publish` the flow is
saved as a draft revision — and an unpublished flow cannot be launched against,
which `rtt launch` also checks.

### `rtt flow list` / `rtt flow pull`

```powershell
just flow-list
just flow-pull BSC_endline                     # writes flows/BSC_endline.json
just flow-pull BSC_endline --out /tmp/snapshots --allow-secrets
```

`pull` scans what it saves and refuses to write a definition containing
credentials unless you insist.

### `rtt template` — WhatsApp content templates

```powershell
just template-list --filter rst
just template-create templates/rst2026_intro.json
just template-create templates/generated --skip-existing --yes
just template-status rst2026_intro
just template-submit rst2026_intro --category UTILITY   # IRREVERSIBLE
just template-delete rst2026_draft --yes
```

Only the **bookends** need Meta approval: the opener, and the closing message to
someone who never replied — both are business-initiated, because that person's
24-hour window never opened. Everything in between is free, and a list picker
**cannot** be approved at all; `template submit` refuses one rather than letting
Meta reject it.

Submission cannot be undone. `delete` exists because Twilio has no update
operation for content, so revising a draft means deleting and recreating; it
refuses anything already submitted.

## Launching

```powershell
just launch "sample.xlsx --columns caseid,name,arm --dry-run"
just launch "sample.xlsx --columns caseid,name,arm --batch-size 50 --sleep 5"
just launch "sample.xlsx --columns caseid,name,arm --resume"
```

| Flag | |
| --- | --- |
| `--columns` | columns to preload into the flow as `{{flow.data.<name>}}` |
| `--flow-id` / `--from-number` | override `.env` for this run |
| `--batch-size` / `--sleep` | pace the send |
| `--resume` | skip numbers already sent successfully, retry only failures |
| `--dry-run` | run every check, send nothing |
| `--skip-preload-check` | bypass **all three** pre-flight checks below, including the never-published block |

Four things it refuses or warns about before sending:

1. **The flow has never been published.** Hard block.
2. **The flow references preloaded values you are not sending.** Named, with a
   case-mismatch hint, because `Name` vs `name` resolves to an empty string
   rather than erroring — every message goes out saying "Hi ," and you find out
   after the round.
3. **Replies to this number reach a different flow.** The check that would have
   saved an afternoon.
4. **A tracker already exists.** Re-running without `--resume` would send to
   everyone a second time and append into the same file unmarked.

The tracker is written next to the input as `<stem>_output.csv` and flushed after
every row, so an interrupted run stays resumable.

## During the round

### `rtt data-check` — high-frequency checks for the data

```powershell
just data-check "responses.csv"
just data-check "responses.csv --key caseid"
```

The data-side twin of `flow-check`. Findings are **warnings, not errors** — by
the time data exists there is nothing left to prevent, and this is meant to run
on a loop during collection.

| Finding | What it catches |
| --- | --- |
| `duplicate-observations` | a respondent with more than one row — usually a re-launch, or a retried publish. You cannot tell which of two disagreeing rows to keep |
| `unjoinable-rows` | a row with no `caseid`, so it cannot be matched back to the sampling frame |
| `no-recognised-outcome` | no row carries `complete`, `declined`, `incomplete`, `unreachable`, `undeliverable` or `optout`, so completion cannot be measured |
| `no-data` | the dataset is empty |

### `rtt fetch` — what does Twilio think happened?

```powershell
just fetch "--since 2026-08-01"
just fetch "--against responses.csv --output missing.csv"
just fetch "--no-answers"
```

Pulls executions from the Studio API and, with `--against`, reports the ones with
no counterpart in your published data — the rows the publish step failed to
write.

Two warnings. Twilio retains execution context for about 30 days, so reconcile
during collection. And **this output is unencrypted**: it comes from inside
Twilio, where the plaintext lives. See [encryption.md](encryption.md).

Any row whose answers could not be read carries a `context_error` column. A
`HTTP 404` there is the ordinary case — the context aged out. Anything else means
the answers still exist and this export is incomplete, which the run says loudly
rather than reporting a short file as finished.

## After the round

### `rtt decrypt`

```powershell
just decrypt "responses.csv"
just decrypt "responses.csv --output clean.csv"
just decrypt "old.csv --columns name,phone --legacy-secret KEY"
just decrypt "responses.csv --to-motherduck round1_clean --warehouse-columns caseid,answer"
```

Encrypted columns are detected by their `v2:` marker, so there is no list of
names to maintain. A value that cannot be decrypted becomes
`<DECRYPTION FAILED>` rather than aborting the file — but plain text in a
partially-encrypted column is passed through untouched, not overwritten.

Writing over the input is refused: the ciphertext is the only copy.

### `rtt push`

```powershell
just push "clean.csv --table round1"
just push "clean.csv --table round1 --columns caseid,city,answer"
just push "clean.csv --table round1 --mode replace"
```

Appends by default. **`--mode replace` issues `CREATE OR REPLACE TABLE`** and
destroys what is there, so it has to be asked for. Prefer `--columns` to leave
direct identifiers out of a shared warehouse.

## Housekeeping

```powershell
just test              # the full suite, incl. the Node-to-Python interop check
just test-cov
just fmt-all           # ruff + markdownlint
just scan-secrets      # private keys; gitleaks runs in CI
just mcp-list          # are the Twilio MCP servers configured?
just build-demo-flow   # regenerate the worked example
just deploy-functions  # deploy the two Twilio Functions
```
