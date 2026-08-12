---
name: studio-flow
description: Use when designing, reviewing, auditing or modifying a Twilio Studio flow for an IPA survey - adding questions, wiring error and timeout handling, deciding what to encrypt, publishing to Google Sheets or MotherDuck, or checking an existing flow before a round. Applies IPA's survey-research conventions (consent, paradata, error aggregation, PII encryption) rather than generic Twilio chatbot practice. Uses the twilio-docs and twilio MCP servers and this repo's `just flow-*` recipes.
---

# Studio flows for IPA surveys

## What this is really about

Twilio Studio is a chatbot builder. IPA uses it to run **survey research**, and
those are not the same discipline. A chatbot can drop a confused user; a survey
has to record *that* it dropped them, *where*, and *why*, or the dataset lies
about its own missingness.

So most of the work in an IPA flow is not the questions. Measured across all 47
flows on the account: 723 question widgets, but 867 `set-variables` and 1259
splits. **Roughly two control widgets for every question.** That ratio is the
retrofit - it is what turns a chat tool into an instrument.

Read `references/ipa-flow-conventions.md` before designing anything. It is the
measured house style: variable vocabulary, timeout defaults, what gets
encrypted, the publish payload shape.

## The six-stage skeleton

Every IPA survey flow follows this spine. Design against it; audit against it.

```
1. CONSENT        identify, state the basis, link the document, record the answer
2. QUESTIONS      each one wrapped in validation + timeout + delivery-failure
3. AGGREGATE      count errors per section, decide when to stop asking
4. ENCRYPT        direct identifiers only, through the encrypt Function
5. PUBLISH        Sheets (dashboard + database of record), then MotherDuck
6. FINALIZE       closing template, status flags written
```

Stages 3 to 6 are what guarantee the data is both **protected where it needs to
be** and **actually retrievable**. Skipping stage 3 gives a dataset that cannot
distinguish "refused" from "never asked". Skipping stage 4 puts PII in a shared
spreadsheet. Skipping stage 6 leaves respondents in silence and rows unflagged.

## Stage 0 - preloaded data, and the sample file

Before any of that: **what the flow already knows about the respondent.**

`{{flow.data.<key>}}` is the Studio equivalent of a SurveyCTO preload. The
values come from the sample spreadsheet at launch - `rtt launch` passes each
column named in `--columns` as an execution parameter, and the flow reads them
back as `flow.data.<column>`.

**The column name and the reference must match exactly.** This is the
highest-frequency silent failure in the whole pipeline:

```
sample file column:   participant_name
flow reference:       {{flow.data.name}}
result:               every message says "Hi ,"  and the published column is blank
```

Nothing errors. Twilio resolves an unknown `flow.data` key to an empty string,
the survey runs to completion, and the damage is only visible in the output
after the round. So the launcher cross-checks before sending:

```bash
just launch "sample_input.xlsx --columns caseid,name,treatment --dry-run"
```

It fetches the flow, extracts every `flow.data.*` reference, compares against
the columns being sent, and reports what is missing - including a case-mismatch
hint, since `Name` vs `name` is the usual culprit. On a real send it asks for
confirmation before continuing; `--skip-preload-check` bypasses it.

### The house vocabulary

Measured across the 47 flows, by how many use each key:

| Key | Flows | What it is |
| --- | --- | --- |
| `name` | 29 | Respondent's first name, for the greeting |
| `caseid` | 26 | **The join key.** Links back to the sampling frame |
| `treatment` | 13 | Assignment arm |
| `p_number_original` | 12 | Phone number as sampled - see below |
| `grupo` | 11 | Group assignment |
| `nationality`, `sexo`, `edad`, `niv_educativo_sisben` | 7 each | Demographics carried from baseline |
| `link`, `enlace`, `link1`, `link2` | 3-5 | Per-respondent URLs |

Rich flows carry a lot: `BSC_intervention` references 26 preloaded keys,
`FMI_screening` 23. Eight flows use none at all.

**`caseid` is the one that matters most.** It is what lets a response join back
to the sampling frame, and without it a completed survey is an orphan.

**`p_number_original` deserves attention.** It is preloaded *and* encrypted -
the number as sampled goes in as data, through the encrypt widget, and lands as
`enc_p_number_original`. That keeps the response linkable to the sample while
the identifier itself stays protected in the Sheet. Note it is distinct from
`Number`, which the launcher uses as the destination address; a respondent may
reply from a different handset, so the two can diverge and both are worth
keeping.

### The sample file

`sample_input.xlsx` is the committed example. One required column plus whatever
the flow preloads:

| Column | Required | Note |
| --- | --- | --- |
| `Number` | **Yes** | Destination. Prefix WhatsApp with `whatsapp:` |
| `caseid` | In practice | The join key |
| `name` | Usually | Greeting |
| `p_number_original` | If encrypting the number | Usually the same digits as `Number`, without the channel prefix |
| ... | | Anything else the flow references |

Rules that avoid the silent failure:

- **Name columns after the `flow.data` keys**, not after what they mean to you.
  The flow is the contract.
- **No spaces or accents in headers.** `flow.data` keys are
  `[A-Za-z0-9_]` only, so a column named `Nombre completo` can never be
  referenced.
- **Everything is a string.** The launcher reads with `dtype=str` so leading
  zeros in IDs and phone numbers survive. Excel will happily turn `007` into
  `7` before it ever reaches the file - check.
- **The sample file is Confidential.** It is a list of phone numbers with names
  attached. `*.xlsx` and `*.csv` are gitignored precisely so real sample files
  cannot be committed.

## Stage 1 - consent

22 of 47 flows carry consent language; the ones that do share a shape.

1. **Identify** IPA and the study in terms the respondent recognises.
2. **State the consent basis** - "dado que nos autorizaste un nuevo contacto".
   This is also what earns the opening template a UTILITY category.
3. **Link the informed-consent document**, do not try to fit it in WhatsApp.
4. **Record the decision** into `set_consent`.
5. **Route a decline to a polite close**, never to silence.

Declining is a valid outcome and must be published like any other. A respondent
who said no is not missing data.

Where the IRB protocol specifies consent wording, that text wins over anything
that reads better.

## Stage 2 - questions

**Prefer buttons and lists. Avoid open text unless the question genuinely needs
prose.**

| Answer type | Use | Why |
| --- | --- | --- |
| Categorical | Quick-reply buttons (up to 10) | One tap, no validation needed, no spelling variants |
| Many options | WhatsApp list picker | Up to 10 rows, scrollable, in-session only |
| Scale 1-10 | Buttons or a validated numeric split | Both work; buttons remove typos |
| Genuinely open | `send-and-wait-for-reply` with free text | Only when the answer cannot be enumerated |

Open text costs three ways: it needs a validation split and an error counter, it
arrives with typos and mixed languages that require cleaning, and it is far more
likely to contain incidental PII a respondent volunteers - which then has to be
encrypted or scrubbed. Buttons return a stable `ButtonPayload` you can branch on
directly:

```
widgets.<widget_name>.inbound.ButtonPayload
```

**Every question wires all three transitions.** In the corpus, `timeout` and
`deliveryFailure` appear exactly once per question widget - there are no
unhandled cases.

| Transition | Wire to |
| --- | --- |
| `reply` | The validation split |
| `timeout` | `set_no_reply_<section>`, then continue or close |
| `deliveryFailure` | `set_fail_<section>`, then stop contacting |

**Timeouts.** House defaults are **4 hours** (230 uses) and **1 hour** (203).
Respondents answer around their day. But a chain of 4-hour timeouts can push the
end of the survey past the 24-hour WhatsApp session window, which is exactly why
the closing message must be a template - see the `whatsapp-template` skill.

**Timestamps.** Write `set_time_start` at the first question and `set_time_fin`
at the close, plus intermediate `set_time_<n>` at section boundaries. Duration is
a quality signal: implausibly fast completion is the standard flag for
inattentive responding.

## Stage 3 - aggregate errors, and know when to stop

The pattern that appears throughout the corpus:

```
question --(reply)--> split_<q>              validate
                       |- match    -> next question
                       |- noMatch  -> increment counter_error_<q>
                                        |
                                        v
                                     split_error_<q>
                                       |- under limit -> error_message_<q> -> re-ask
                                       |- over limit  -> set_multierror_<section>
                                                         -> leave the section
```

**Three status flags per section**, published as columns:

| Flag | Set when |
| --- | --- |
| `set_multierror_<section>` | Too many invalid answers; gave up asking |
| `set_no_reply_<section>` | Timed out |
| `set_fail_<section>` | Delivery failed |

**Scope them by section, not globally.** `edutainment_bl` carries the full set
for `dem`, `obs`, `mig`, `emp` and `social`. That is what lets an analyst say
*where* a respondent broke off. A single global flag cannot.

**The stop rule matters ethically, not just technically.** Re-asking a
respondent who cannot answer is badgering someone who has already given their
time. Pick a limit - two or three attempts - and honour it. Then record that the
question was abandoned, so the analyst sees an explicit `multierror` rather than
a blank that looks like a skip.

`deliveryFailure` should stop contact for that respondent entirely, not just that
question. Repeated sends to a dead number burn quota and can trigger spam
signals against the sender.

## Stage 4 - decide what to encrypt

**Encrypt direct identifiers. Leave analysis variables in clear.**

The corpus is consistent about this. Encrypted: `name`, `p_number_original`,
`Number`, `phone*`, `address`, `localidad`, and date-of-birth parts
(`verif_dob_*`, `year`, `month`). Published in clear: `age`, `gender`,
`education`, `ciudad_principal`, `assigned_group`, `caseid`.

That split is deliberate and correct. Encrypting analysis variables would make
the Google Sheet useless as a monitoring dashboard while buying little - the
identifiers are what re-identify someone.

Judgement calls:

- **Free-text answers.** Encrypt them if respondents might name people or
  places, which they do in feedback and complaint questions. The RST 2023 flow
  published free-text criticism of named training sessions in clear.
- **Date of birth.** The corpus encrypts it. Combined with location it is
  quasi-identifying, so follow that.
- **Small-area geography.** `localidad` is encrypted; `ciudad_principal` is not.
  City is fine, neighbourhood is not.

Naming: the published column takes an **`enc_` prefix**.

```
enc_p_number_original = {{widgets.function_encrypt.parsed.enc_p_number_original}}
```

Place the encrypt Function widget **immediately before the publish widget**, so
nothing can be added later that publishes around it.

## Stage 5 - publish

**The point of `publish_gsheets` is that it fires per submission.** Each
execution appends its own row the moment that respondent's flow reaches the
widget, so the sheet fills in real time as answers arrive. That is what makes it
a live delivery dashboard rather than an export: the field team watches rows land
during collection, no batch job, no waiting for the round to end. It is also why
Sheets is the database of record and not merely a copy.

Two things follow from that, and both are load-bearing.

### One publish widget, and every path must reach it

All 29 publishing flows on the account carry **exactly one** publish widget -
`edutainment_bl` funnels 471 widgets into a single call with 95 parameters. That
is the right shape. Every terminal path routes through it: completed, timed out,
too many errors, delivery failed. One exit point means a row exists whatever
happened, carrying the paradata flags that say which of those it was.

**A path that misses the publish widget produces no row at all**, and a
respondent who broke off becomes indistinguishable from one who was never
contacted. That is the difference between measured attrition and missing data,
and it is silent - nothing errors, the row simply never appears.

`rtt flow pull` checks this by walking the graph backwards from the publish
widget and reporting any `timeout` or `deliveryFailure` transition that cannot
reach it:

```text
  6 break-off path(s) never reach the publish widget:
    verif_1_pilot --timeout--> verif_1_rem1_pilot
    verif_1_rem1_pilot --timeout--> verif_1_rem3
    ...
```

`BSC_baseline` and `BSC_screening` both have six such paths today, in the pilot
verification branch. Everyone who timed out there is simply absent from the data.

### Every respondent must end with a final status

This is the requirement the whole design serves: **at the end of a round, every
launched respondent has exactly one row with a final status.** Nobody is
unaccounted for.

The statuses are exhaustive, and they are what an analyst reads to reconstruct
what happened:

| Final status | Meaning | Set on |
| --- | --- | --- |
| `set_complete` | Reached the end of the questionnaire | Normal exit |
| `set_no_reply_<section>` | Stopped replying | `timeout` |
| `set_fail_<section>` | Message could not be delivered | `deliveryFailure` |
| `set_multierror_<section>` | Too many invalid answers; gave up asking | Error-counter limit |
| `set_consent` = declined | Refused to participate | Consent branch |

Two records together account for everyone:

- **The delivery tracker** (`<sample>_output.csv`, written by `rtt launch`) says
  whether the flow was ever started for that number.
- **The published sheet** says what happened once it was.

A respondent in the tracker with no row in the sheet is the case to hunt: either
a break-off path that misses the publish widget, or a Sheets write that failed
after its retries. `just fetch --against` distinguishes them.

**Refusal and break-off are findings, not gaps.** A survey that only records
completions cannot report its own response rate, and cannot tell a reviewer
whether the people who left differ from the people who stayed. That is why the
paradata is roughly half the published columns.

### It fires once, so it must fire at the end

Because there is a single publish call, a respondent who abandons before
reaching it publishes nothing - even though they answered fifteen questions. The
paradata flags are what recover that: the timeout path sets
`set_no_reply_<section>` and then continues to publish, so the row lands with
the answers given so far and a flag saying where it stopped.

Wire break-offs *through* the publish widget, never around it.

### A better mechanism, later

Publishing from a Function widget at the end of the flow is a retrofit. Twilio
Event Streams can emit each execution step as a webhook, which would give
genuinely per-answer streaming, survive a flow that never reaches its end, and
remove the single point of failure that a Sheets API error currently represents.
Worth doing; not done here. Until then, reconcile with `just fetch --against`.

### The payload

Four groups - copy this order:

```
caseid, assigned_group        launch data from flow.data
age, gender, education        analysis variables, clear
enc_<identifier>              PII, through the encrypt widget
set_multierror_<section>      paradata, three per section
set_no_reply_<section>
set_fail_<section>
set_time_start, set_time_fin  timestamps
```

**Paradata is roughly half the columns, and that is right.** Without it the
dataset cannot describe its own missingness.

Publishing can fail. `publish_gsheets.js` retries once, then drops the row
silently. Reconcile after each round:

```bash
just fetch "--against responses.csv --output missing.csv"
```

Then load the analysis copy:

```bash
just decrypt "responses.csv --to-motherduck survey_round_1"
```

## Stage 6 - finalize

- Send the **closing template** (not a free-form message - the window may have
  closed; see `whatsapp-template`).
- Write `set_complete`.
- If nobody monitors the number afterwards, say so in the close.

## Using the MCP servers

Both are configured in `.mcp.json`.

**`twilio-docs`** - unauthenticated, read-only. Use it rather than guessing at
widget schemas or error codes:

- `twilio__search` with `source="docs"` for concepts, `source="api"` for
  endpoints, `product="studio"` to scope.
- `twilio__retrieve` with an id from a search result for full parameter schemas.

Worth searching before designing: widget transition names, the split-based-on
condition types, `ButtonPayload` handling, and any error code you hit (63016 for
the session window, 92005 for a missing ContentSid).

**`twilio`** (`@twilio-alpha/mcp`, scoped to `twilio_studio_v2` plus content) -
authenticated, can read and modify flows. It is a Twilio **proof of concept** at
v0.7.0, so:

- Fine for inspecting flows, listing executions, and exploratory changes.
- **Do not use it to modify a published flow during live collection.** Use the
  Studio UI, where changes are reviewable, or the API deliberately.
- Twilio advises against running community MCP servers alongside it, because
  content from another server could steer a tool call against the account.

Nothing in this repo depends on the MCP - `rtt` talks to the API directly.

## Auditing an existing flow

```bash
just flow-list                        # what exists, status, revision
just flow-pull <name>                 # into flows/, committed and diffable
```

`rtt flow pull` reports widget and question counts and warns when a flow has
Function widgets but nothing that looks like encryption. It refuses to write a
definition containing credential-shaped strings, since these files are meant to
be committed.

The checklist:

- [ ] **Every path reaches the publish widget** - `rtt flow pull` reports the
      ones that do not. A break-off that publishes nothing is invisible
- [ ] **Every terminal state sets a final status** - complete, no_reply, fail,
      multierror, or consent declined. No respondent unaccounted for
- [ ] Consent recorded, and declining routes to a close *through* publish
- [ ] Every question wires `reply`, `timeout` and `deliveryFailure`
- [ ] Every split has a `noMatch` branch
- [ ] Error counters exist, with a stop rule
- [ ] Status flags scoped per section, not global
- [ ] Direct identifiers encrypted, `enc_` prefix, encrypt before publish
- [ ] Publish payload includes paradata and timestamps
- [ ] Opening and closing messages use content templates
- [ ] Timestamps written at start and finish

Ten flows currently publish to Sheets with no encryption widget - the list is in
`references/ipa-flow-conventions.md`. Some are legitimately identifier-free;
`extortion_survey` is not obviously one of them.

## Naming

Prefer the English forms in new flows - `function_encrypt`, `publish_gsheets`,
`wait_*` - over the older Spanish ones (`encriptador`, `espera_*`). Both are in
use; the English names are what newer flows use and what `rtt flow pull` looks
for when checking whether a flow encrypts.

Widget names become variable references (`widgets.<name>.inbound.Body`), so
renaming one breaks every reference to it. Name it correctly the first time.

## The Python underneath

`src/requests_to_twilio/flows.py`:

| Function | Does |
| --- | --- |
| `list_flows(client)` | All flows, newest activity first |
| `resolve_flow(client, id)` | By SID or friendly name. Re-fetches by SID because the list endpoint does not populate `definition` |
| `scan_for_secrets(definition)` | Credential-shaped strings, SIDs excluded |
| `summarize(definition)` | Widget counts, questions, functions, whether it encrypts |
| `pull(...)` | Fetch, scan, write formatted JSON |
