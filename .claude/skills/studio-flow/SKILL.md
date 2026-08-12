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

Google Sheets is both the **delivery dashboard** and the **database of record**;
MotherDuck is the analysis warehouse. Sheets first, warehouse after.

The payload has four groups - copy this order:

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

- [ ] Consent recorded, and declining routes to a close
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
