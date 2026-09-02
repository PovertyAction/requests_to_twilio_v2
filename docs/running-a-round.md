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
| `opening-cannot-open-session` † | a list picker as the opener — it cannot be approved, so it cannot open a conversation |
| `unmatchable-condition` | a split option no reply can ever reach. Evaluated, not read |
| `split-without-nomatch` | a split with no fallback, so an unexpected reply goes nowhere |
| `too-many-options` † / `text-too-long` † / `text-may-truncate` † | past a Twilio limit; the create call fails with a generic error that names nothing |
| `respondent-initiated-start` | writing to the number starts the survey, so someone can begin a round nobody launched |
| `no-final-status` | publishes with no outcome variable, so completion cannot be told from break-off |
| `no-derived-final-status` | outcomes are recorded, but only as separate `set_*` flags. Those are `1` or **blank**, never `0`, so "not complete" is encoded as absence and reads the same as a dropped column. Strongly suggested rather than required — how you compose an outcome is your business |
| `unpaired-answers` | an answer with no status beside it, so a blank cannot be read as timed-out vs not-asked |
| `no-encryption` | publishes with no encryption widget, so identifiers reach the warehouse in clear |
| `credentials` | a SID or token inside the definition |

**† needs the account.** Four checks read each template's *content type*, which
only Twilio can answer, so they are **silently skipped when the target is a local
file**. `just flow-check flows/my_round.json` reporting "all checks passed" means
17 of 21 passed and four did not run; the command now says so. Run it against
the deployed flow by name — `just flow-check my_round` — before a round, because
a list-picker opener passes on disk and cannot open a conversation.

### `rtt flow schema` — the shape the destination needs

```powershell
just flow-schema flows/data_use_demo_en.json --table my_db.main.round1  # MotherDuck
just flow-header flows/data_use_demo_en.json                            # Google Sheets
```

Derived from the publish widget's parameters, so the destination is a function of
the instrument rather than something maintained alongside it. **Run it after
every instrument change.** Both publish Functions write only into columns that
already exist — a table column, or a header cell in row 1 — so a new question
with nowhere to go is **dropped behind an HTTP 200**, into a row that looks
complete.

Both Functions log the names they had to drop, so the Twilio Console tells you
this happened. Nothing in the data does.

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
just flow-pull my_endline                      # writes flows/my_endline.json
just flow-pull my_endline --out /tmp/snapshots --allow-secrets
```

`pull` scans what it saves and refuses to write a definition containing
credentials unless you insist.

### `rtt template` — WhatsApp content templates

```powershell
just template-list --filter rst
just template-create templates/data_use_demo_intro_en.json
just template-create templates/generated --skip-existing --yes
just template-status data_use_demo_intro_en
just template-submit data_use_demo_intro_en --category UTILITY   # IRREVERSIBLE
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

### `just intake` / `just send` — one path in

`rtt launch` validates that `Number` and `caseid` are present and that `caseid`
is neither blank nor duplicated. It does **not** validate the value in `Number` —
no country check, no `whatsapp:` prefix check. Whatever is in that cell goes to
Twilio, and a malformed one fails per-row at send time, which on the day is 13:55
with a room waiting.

So a sample that reached `rtt launch` by any other route was never checked at
all, and these two recipes exist to be the only route:

```powershell
just intake scripts/build_rst2026_sample.py                          # export -> sample
just intake scripts/build_rst2026_sample.py "--prefix RST2026-TEST"  # a rehearsal, kept apart
just send rst2026_sample caseid,name,arm "--dry-run"   # checks, sends nothing
just send rst2026_sample caseid,name,arm               # sends, then watches for an hour
```

**Numbers are collected somewhere else.** A form, a partner's spreadsheet, a
panel, last round's completers — and they arrive in whatever shape that place
produced. `intake` is where whatever arrived becomes something safe to send, and
it is the only place that check can live, because `rtt launch` verifies that
`Number` is *present* and never what is *in* it.

So messy input is the expected case rather than a failure. Rows that do not
resolve are reported with a reason and written to
`<out>_needs_human_review.csv`; nobody is sent anything; and re-running after a
fix moves nobody already assigned, because `caseid` and `arm` are frozen across
rebuilds. The loop is: export, intake, read the review file, fix at the source,
intake again.

Both recipes take the round rather than assuming one. The builder is the script
that knows one source's shape — which column holds the number, which is the
consent tick — and that is per-source by nature, because a form belongs to
whoever made it. `just send` takes the sample's name without its extension (the
tracker is always `<sample>_output.csv`, because that is what `rtt launch`
writes) and the columns the flow should receive. `caseid` earns its place there
— it is the non-identifying key everything downstream joins on, which is what
confines phone numbers to the master list — but the rest is yours. `arm` belongs
to a study that randomises, and most rounds do not.

`just send` does two things, because during a session they are one action: it
launches, and then it polls for an hour and rewrites the `tracking` tab every
two minutes, so the round is visible to people who are not at a terminal. Ctrl-C
ends the watch without affecting anything already sent — the tracker file on disk
is the record, and `just send` again picks up whoever signed up meanwhile.

Two choices in there worth knowing. `--every 2` rather than every minute because
polling repeatedly earns a 429 eventually, and a rate limit in front of a room is
worse than a two-minute refresh. And `--full-window`, without which the watch
would end almost immediately — see below, it is not obvious.

`intake` reads the export and writes a launch sample. Five things it
does that a hand-made spreadsheet does not:

| | |
| --- | --- |
| **Country comes from the form, per row** | A bare 10-digit string is an Indian mobile, a US line and a Colombian mobile at once, and only the respondent can say which. The export's country column decides; a row that does not say, and whose number carries no `+`, is **reported for a human rather than resolved**. Resolving it on a default region is how a message reaches a stranger. |
| **Consent is required** | An unticked box is not a yes. Those rows are excluded and counted. |
| **A landline is refused** | It parses to valid E.164 and WhatsApp still cannot reach it, so sending is a message that never arrives. `FIXED_LINE_OR_MOBILE` **is** accepted — that is what the library answers when a numbering plan does not separate the two at all, which is every number in `+1`. |
| **`caseid` and `arm` never move** | A rebuild carries every number's existing assignment forward and gives new ones only to new sign-ups. `--resume` keys on `caseid`, so an id that shifted when three more people signed up would re-send to somebody already contacted; an arm that shifted would move a respondent between treatments after they had answered. |
| **An explicit `Number` column is trusted, but cross-checked** | The workbook kept on the day carries the form's columns *and* a `Number` column beside them. If both resolve to the same thing, fine. If they disagree, one is a typo and nothing here can tell which — so the row is reported rather than sent on a coin flip. A `Number` **is** allowed to rescue a row whose country cell could not be read, because it carries its own country. |

Parsing is [libphonenumber](https://github.com/google/libphonenumber) via
`phonenumbers`, not a table of per-country mobile lengths. That is what makes a
trunk zero on an Indonesian `08…`, an eight-digit Singapore number and a country
code typed without a plus all resolve correctly — and it handles the trap worth
knowing: Indian mobiles start with 6-9, so an ordinary local number like
`9123456789` **begins with the country code 91**, and any rule that reads the
prefix to decide "it already has its country code" turns it into a ten-digit
`+9123456789` that Twilio rejects at send time.

### Which column is which

Columns are found by hint, and two of the form's own headers are traps that hint
matching walks straight into:

- **`Organization Name` contains "name".** Read as the respondent name, every
  opener goes out addressed to an employer.
- **`I agree to receive one WhatsApp message from IPA...` contains "WhatsApp".**
  Read as the phone column, every row fails to resolve at once.

Today the hint *order* happens to save both, because `First Name` is matched
before the bare `name` hint and the number column sits above the consent
question. That is luck about column order, not a property — so each role also
carries an explicit list of headers it must never match, and moving or renaming a
question cannot silently repoint it. There is a test for each.

The country question is free text rather than a dropdown, so the cell is read as
widely as can be done safely: a dial code (`+233`, `233`, `0091`), an ISO code
(`GH`), or a country name with accents and punctuation ignored, so `Cote d
Ivoire` and the properly spelled version are one key. A **dial code works for
every country on earth** — that path goes through the library's own code-to-region
lookup. A **name** only works if it is in the table in the script, which is
hand-kept and deliberately not exhaustive; a name that is not there is reported
with the remedy in the message ("put the dial code in the country column"),
never guessed at. Add to that table freely — it is a list, not a design.

Nothing the report prints contains a phone number. Numbers are Confidential
under IPA's data classification, and a report is the kind of thing that gets
pasted into a chat window.

### Your own IDs, and what Twilio actually needs

**Twilio needs the number.** Nothing else about a respondent is required to
reach them. Everything in `--columns` beyond that is a flow *parameter*, and a
parameter is only worth sending if the flow reads it: `name` because the opener
greets somebody, `arm` because the instrument branches on it, `caseid` because
it is the key the submission carries back. An identifier the flow never reads is
a value published to a third party for no reason at all.

So if your sample already has a `student_id`, a `household_id`, or whatever the
study calls its unit, **leave it out of `--columns`.** It stays in the sample and
never leaves the machine.

That works because the sample may carry more than the flow receives, and
`--columns` is the boundary:

```powershell
# sample on disk:  Number, caseid, name, student_id, site
just send wave3 caseid,name        # only caseid and name become flow parameters
```

`rtt launch` reads the whole file, checks that every *requested* column exists,
and builds the parameter set from that list alone. Columns it was not asked for
are carried nowhere. Join your own ID back on `caseid` after `rtt decrypt` — the
sample is the master list, and it is already one of the two files allowed to
hold numbers.

Two reasons point the same way here, which is why this is the default rather
than a preference. The first is that Twilio has no use for the ID. The second is
that the tool has to assume you know not to put identifying information in your
own ID column — it cannot inspect what a study calls its unit — and keeping the
column local means that if that assumption is ever wrong, the mistake does not
leave the machine.

#### If you do send PII, encrypt it first

Sometimes there is a real reason — a value that has to land in the published
dataset next to the answers, and the flow is the only thing writing there. In
that case encrypt the column **before** it goes into the sample, with the same
public key the flow uses:

```python
from requests_to_twilio import crypto

pub = crypto.load_public_key(public_key_b64)  # the key `just keygen` printed
frame["guardian_name"] = [crypto.encrypt(v, pub) for v in frame["guardian_name"]]
```

The ciphertext is the same `v2:` sealed box that `encrypt_fields.js` produces
inside the flow, so nothing downstream needs to be told about it. `rtt decrypt`
detects any column carrying the `v2:` marker and needs no column list:

```text
ciphertext prefix          : v2:  (95 chars for a short name)
auto-detected as encrypted : ['guardian_name']
round trip intact          : True
```

Encrypting on this side is **stronger than encrypting in the flow**, and it is
worth knowing why. `encrypt_fields.js` protects a value the respondent typed,
which means the plaintext existed in a widget before the encrypt function ran. A
value you seal on your own machine is ciphertext for its whole life in Twilio —
the API call, the execution context, the published row. Twilio never holds the
plaintext at all.

The constraint is that **the flow cannot read what it cannot decrypt.** This works
for a value the flow only passes through to the store. It does not work for
anything the flow acts on: an encrypted name cannot greet somebody, and an
encrypted `arm` cannot branch. Those have to stay plaintext, which is the real
argument for sending as few of them as possible.

### `rtt launch` — the general case

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
| `--resume` | skip respondents already sent successfully, retry only failures |
| `--dry-run` | run every check, send nothing |
| `--skip-preload-check` | bypass **all three** pre-flight checks below, including the never-published block |

The sample file needs a `Number` column and a **`caseid`** column. `caseid` is
required, not optional: the tracker, the delivery log and the published row are
all keyed on it, so that none of them has to store a phone number. Blanks and
duplicates are refused up front — a duplicate is the dangerous one, because it
is the key every other file joins on and it silently merges two respondents'
delivery status into a single row.

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
every row, so an interrupted run stays resumable. It holds
`caseid, status, execution_sid, url, error, sent_at` — **no phone number**. It
used to carry one twice, as `number` and again as `contact`, in the file most
likely to be mailed around while a round is live.

## During the round

Three views, and they see different things. Reach for the one that matches the
question:

| Question | Command | Reads |
| --- | --- | --- |
| Did it land, and are they answering? | `rtt monitor` | the Messages API |
| Are the executions progressing? | `rtt fetch` | Studio executions |
| Is the collected data sound? | `rtt data-check` | the published table |

The order matters when something looks wrong. A respondent missing from the
published table might have broken off — or their message might never have been
delivered, in which case there is no execution and no row to be missing from.
Only `rtt monitor` can tell you which.

### `rtt monitor` — did the round actually land?

```powershell
just monitor "--tracker sample_output.csv --sample sample.xlsx"
just monitor "--tracker sample_output.csv --sample sample.xlsx --hours 4"
just monitor "--tracker sample_output.csv --sample sample.xlsx --sheet --every 1"
```

One row per **respondent**, not per message — a round of 4 people produces around
70 messages and nobody watching a live round wants 70 rows. Each respondent
holds one state:

| State | Meaning |
| --- | --- |
| `failed` | the opener did not go out, or came back undelivered |
| `sent` | accepted by Twilio, not yet confirmed on the handset |
| `delivered` | it arrived |
| `answered_back` | they replied, so the flow has taken over |
| `unsolicited` | they wrote in without being launched |

`failed` and `answered_back` are final and stop being polled. When everyone
has settled the loop stops on its own rather than spending rate limit on a
finished round.

**That means the monitor can exit within a minute on a small round, and it does
not mean the survey finished.** Somebody who has `answered_back` is still
working through the questions; delivery simply has nothing further to say about
them. Their data row appears only when the flow reaches its publish widget at
the end. For where a respondent is mid-survey use `rtt fetch`, which reads
execution state.

**Pass `--full-window` when somebody is watching.** It keeps polling for the
whole `--hours` window even once every number has settled, which is the only way
the tracking tab keeps moving through a live session rather than freezing a
minute after the send. The default is the right one for reconciling a round
afterwards; this is the flag for doing it in front of a room, and `just send`
passes it.

**Pass `--tracker`.** It scopes the poll to one round using the launcher's own
`sent_at`, and it is also the only place a send that never left is recorded —
those are reported first, because no message exists for them to appear in any
delivery status. A date is the wrong unit: `--since` at day resolution once
returned 91 messages for a round of 4.

**Pass `--sample`.** It is the master list, and the only file this command reads
a phone number from. The Messages API can answer only in phone numbers, so the
mapping to caseid is built from the master list in memory and nothing written
carries a number — see [publishing.md](publishing.md). Without it every
respondent is filed under `unknown-<digest>`, which is safe and useless to
watch.

**Pass `--sheet`** to rewrite a Google Sheet tab after every poll, so the round
is visible to people who are not at a terminal. `--sheet-tab` chooses which,
default `tracking`. The tab must already exist.

Two things worth knowing about what it reports:

- **An error code on an *inbound* message is a failure**, even though its status
  reads `received`. That means the reply reached Twilio and Twilio could not
  hand it to the flow — error `11200` is a webhook returning non-2xx. The answer
  is gone, and every other surface reports success.
- **A rate limit is never reported as an empty round.** Polling repeatedly earns
  a 429 eventually, and "no messages" at that moment would describe a quiet
  healthy round when the account is at its busiest.

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
| `no-recognised-outcome` | no row carries a value from the outcome vocabulary below, so completion cannot be measured |
| `no-data` | the dataset is empty |

#### The two status columns

Every published row carries both, and they answer different questions.

**`outcome` — which terminal path the flow took.** Six values, written by the
widget that ends the execution:

| `outcome` | |
| --- | --- |
| `complete` | reached the end |
| `declined` | said no at consent |
| `incomplete` | answered some, then stopped replying |
| `optout` | sent a stop word mid-survey |
| `unreachable` | never replied at all |
| `undeliverable` | the first message never arrived |

`optout` is deliberately separate from `incomplete`: someone who asked to stop is
exercising a right, not breaking off, and collapsing the two overstates attrition
while burying a consent signal.

**`final_status` — what the pipeline ended up with.** Four values, derived at
`finish`, the one widget every terminal path passes through:

| `final_status` | From |
| --- | --- |
| `complete` | `complete` |
| `declined` | `declined` |
| `incomplete` | `incomplete`, `optout`, `unreachable` |
| `failed` | `undeliverable`, or encryption failed |

This is the column to group by. **`failed` means the system let us down, never
the respondent** — a refusal, an opt-out and a silence are all things a person
is entitled to do, and none of them is a failure.

Both vocabularies are declared once, in `src/requests_to_twilio/outcomes.py`,
and the Liquid the flow runs is generated from that same mapping. They were
previously written out in three places and had already drifted: the flows
emitted `unreachable`, `undeliverable` and `optout` long before the data checks
recognised them, so a round of pure non-response was reported as having no
recognisable outcome at all.

**One thing `final_status` cannot tell you.** A send that Meta rejects or the API
refuses never becomes an execution, so it publishes no row at all — that person
is *absent* from the dataset rather than `failed` within it. Absence is the one
state a column cannot express. `rtt monitor --tracker` is what surfaces them.

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

### `just round-reset` — clearing a round, keeping a template

```powershell
just round-reset rst2026_sample.xlsx                             # report only, changes nothing
just round-reset rst2026_sample.xlsx "--snapshot --truncate"     # dry run of the real thing
just round-reset rst2026_sample.xlsx "--snapshot --truncate --yes"
just round-reset rst2026_sample.xlsx "--local old_output.csv --yes"  # named leftovers
```

The sample is required rather than defaulted, and `--round` derives the other
two files from it: `<sample>_output.csv` and the shared `delivery_log.csv`. A
default would be a filename from somebody else's round, and the failure mode is
deleting the wrong one silently or the right one not at all. Pass `--signups` to
name the hand-maintained export so it is never deleted.

Three operations, chosen independently, and **nothing happens without `--yes`**.

`--snapshot` copies `data` to `data_template` and `tracking` to
`tracking_template`. It exists because a dashboard needs rows to be built
against, and the rows a rehearsal produced are the only honest sample of what a
real round looks like — so they can be kept while the live tabs go back to
empty, and the dashboard repointed once it works.

`--truncate` deletes every row below the header with one `deleteDimension`,
rather than clearing the tab and writing the header back. The difference is the
failure in between: a clear that succeeds and a write that then fails leaves a
tab with **no header row**, and `publish_gsheets` maps a parameter to a column by
matching row 1 — so the next submission would have nowhere to go and would be
dropped behind an HTTP 200.

`--local` deletes **exactly the files you name** — there is no default list.
Run the command with no operations first: it reports every data-shaped file in
the working directory (`*_output.csv`, `*_decrypted.csv`, `*_export.csv`,
`*.xlsx`) so you can see what is there and choose. A decrypted export is
plain-text PII and is the one worth not leaving lying around.

There was a default list, and it was one operator's rehearsal filenames — so
every other user was shown files from somebody else's laptop and told they were
"local test artifacts". `sample_input.xlsx` and `sample_template.xlsx` are never
deleted, nor is anything named by `--signups`: both are committed reference
material rather than data from a round, and the sign-up export is
hand-maintained.

Only four tabs are ever read or written, so a sign-up form whose responses land
in the same workbook cannot be caught by this.

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
