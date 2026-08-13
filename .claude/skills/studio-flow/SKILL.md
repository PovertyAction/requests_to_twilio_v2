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

### The option-count rule: 3 buttons, 10 list rows

**Never more than 3 quick-reply buttons, never more than 10 list rows.** Both
are hard, and `rtt flow check` reports `too-many-options` if a flow breaks
either.

- **10 rows** is Twilio's limit on `twilio/list-picker`. There is no way around
  it.
- **3 buttons** is the limit that applies to *us*. Twilio allows up to 10
  quick-reply buttons, but only on a template Meta has approved; sent in session
  without approval, WhatsApp permits 3. Since every question template here is
  deliberately never submitted, 3 is the real ceiling for anything after the
  opener. Do not "fix" this rule after reading 10 in Twilio's docs - the 10 case
  is one we never use. The opener is the exception, because it is approved.

Past the limit WhatsApp does not truncate politely; the send fails.

There is also a research reason to sit well under both. Every option past the
first few is another scroll on a phone, and options nobody scrolls to are
options nobody picks - which shows up as a skew in the marginals, not as an
error. **A question needing more than ten answers needs splitting, not a longer
list.** If the option set is genuinely long (districts, occupations), ask a
coarse question first and branch to a shorter list.

| Answer type | Use | Limits |
| --- | --- | --- |
| 2-3 options | `twilio/quick-reply` buttons | **3 max** in session. Title 25 chars |
| 4-10 options | `twilio/list-picker` | **10 max**. Item 24 chars, description 72 (required), button 20 |
| More than 10 | Split the question | Branch to a second, shorter list |
| Scale 1-10 | List picker, or a validated numeric split | Both work; the list removes typos |
| Genuinely open | `send-and-wait-for-reply` with free text | Only when the answer cannot be enumerated |

Open text costs three ways: it needs a validation split and an error counter, it
arrives with typos and mixed languages that require cleaning, and it is far more
likely to contain incidental PII a respondent volunteers - which then has to be
encrypted or scrubbed.

**Interactive messages are nearly free, and this is the single most useful thing
to know here.** Both types are Content templates, but *neither needs Meta
approval to be used as a reply*:

| Content type | In session (24h window) | Business-initiated (opener) |
| --- | --- | --- |
| `twilio/text` | free | needs approval |
| `twilio/quick-reply` | free, max 3 buttons | needs approval, up to 10 |
| `twilio/list-picker` | free | **not supported at all** |

So only the **opening message** ever goes to Meta. Everything after the
respondent's first reply - consent, every question, every nudge - can be a
button or a list created this afternoon and used immediately. Create the
template with `just template-create` and simply never submit it.

Two consequences worth internalising:

- A list picker **cannot open a conversation**. Put one on the first widget and
  every respondent in the round gets error 63016 and nothing else runs. `rtt
  flow check` catches this as `opening-cannot-open-session`, and
  `opening-not-a-template` catches a free-form body in the same position.
- `rtt template submit` **refuses** a list picker. WhatsApp does not reject the
  request, it simply never resolves - which looks exactly like a slow approval
  on the morning you need it.

### Reading the answer: tap or type

A tap does not return a special event. It returns an ordinary inbound message:

| Interaction | `inbound.Body` | `inbound.ButtonPayload` |
| --- | --- | --- |
| Quick-reply tapped | the button's **title** | the button's `id` |
| List row tapped | the row's **item** text | - |
| Typed instead | whatever they wrote | - |

Branching on `ButtonPayload` only works for quick replies and only for people
who tapped. So **split on `inbound.Body`, accepting both the label and its
position**, and the person who ignores the menu and writes `3` is matched
identically.

**Use the `regex` predicate for option lists, not `matches_any_of`.** This is a
correctness rule, not a preference:

```
type:  regex
value: (?:\s*(?:0 times|\(?1[.)]?|1-2 times|\(?2[.)]?|More than 10 times|\(?5[.)]?)\s*)
```

`matches_any_of` takes its alternatives as **one comma-delimited string**. A
comma inside an option label silently becomes two alternatives, neither of which
is the label - the respondent taps a real option and lands on noMatch. That is
the "answer looks fine, respondent gets stranded" defect in its purest form, and
it is invisible on the canvas. In a regex a comma is just a character.

Three things about Studio's predicates that are not obvious, all documented:

- Conditions are **already case-insensitive** and **already trim surrounding
  whitespace**. Neither is what breaks; do not add machinery for them.
- `regex` is written **without slashes**, is case-insensitive, and **must match
  the entire string**.
- Because the anchoring wraps whatever you supply, **bare alternation binds
  wrongly**: `a|b` can behave as `(^a)|(b$)` and match `xxb`. Always wrap the
  whole pattern in `(?:...)`.

Two more rules for option labels:

- **Escape regex metacharacters** when building the pattern - `(CAPI)` is a
  group, not a literal. Escape only the universally special characters; Python's
  `re.escape` also escapes a space as `\ `, which is an error in a JavaScript
  unicode-mode regex, and you do not control Studio's engine.
- **No emoji in a label.** It is compared literally after a round trip through
  WhatsApp; variation selectors make two identical-looking labels different
  strings. Put the warmth in the body, which nothing matches on.

### Do not read conditions - run them

`rtt flow check` reports `unmatchable-condition` for a regex that does not
compile or a `matches_any_of` with an empty alternative (the fingerprint of a
comma inside a label). Studio accepts both and simply never fires them.

Better, `requests_to_twilio.flows` exposes `evaluate_condition` and
`route_split`, which implement Studio's semantics, so a test can push every
possible reply through the real split widget and assert where it lands:

```python
split = states["split_ARM2_P1"]
for index, (_, label, _) in enumerate(options, start=1):
    assert route_split(split, label) == "store_ARM2_P1"
    assert route_split(split, str(index)) == "store_ARM2_P1"
assert route_split(split, "banana") == "retry_ARM2_P1"
```

Assert the negative case too. A pattern loose enough to match anything is worse
than one that matches too little: junk is stored as a real answer, the
respondent is never re-asked, and the row looks complete.

**Store a normalised code, not the raw reply.** Otherwise the column is half
labels and half digits depending on how each person answered. Publish both: the
raw `inbound.Body` and a `_code` derived in a `set-variables` widget.

```liquid
{% assign reply = widgets.ARM2_P1.inbound.Body | strip | downcase | replace: ".", "" | replace: ")", "" | replace: "(", "" | strip %}{% case reply %}{% when "0 times" or "1" %}1{% when "1-2 times" or "2" %}2{% else %}other{% endcase %}
```

**The mapping must be exactly as tolerant as the split.** Liquid `case` is an
exact comparison, so if the regex accepts `1.` and the mapping does not, the
respondent is recorded as having answered while their answer codes as `other` -
which reads in the data as a broken option rather than as the tolerance working.
Normalise with the same filter chain, and assert the two agree.

Publishing both matters because the code is *derived*: if the Liquid ever fails
to render, the answer itself is still in the row.

`scripts/build_data_use_demo.py` generates all of this - template, split
condition and code mapping - from one option table, so the message a respondent
sees and the value stored for them cannot disagree.

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
reach it. `flows/` is gitignored - pulled definitions are snapshots of Twilio
state, re-fetchable at any time:

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

## Checking a flow - the HFC equivalent

`just flow-check` is to a Studio flow what
[high-frequency checks](https://github.com/PovertyAction/high-frequency-checks)
are to a SurveyCTO round. It does not look at collected data; it verifies the
**instrument was coded correctly**, so that the data it produces will be
analysable. Run it before a round starts and after any edit.

```bash
just flow-check                       # every flow on the account
just flow-check "edutainment_bl"      # one flow
just flow-check "--errors-only"       # suppress warnings
just flow-list                        # what exists, status, revision
just flow-pull <name>                 # into flows/ (gitignored) for review
```

It exits non-zero on any error, so it can gate a deployment.

### What it checks

| Code | Severity | Why it matters |
| --- | --- | --- |
| `unpublished-paths` | error | A break-off that never reaches publish produces no row, so it is indistinguishable from someone never contacted |
| `no-final-status` | error | A published row that cannot say how the survey ended cannot support a response rate |
| `unhandled-timeout` | error | A question with no timeout branch strands non-responders |
| `unhandled-delivery-failure` | error | Same, for numbers that cannot receive |
| `opening-not-a-template` | error | A free-form first message fails with 63016 for the whole round at once |
| `opening-cannot-open-session` | error | A list picker or location cannot start a conversation, only continue one |
| `too-many-options` | error | More than 10 list rows, or more than 3 buttons in session - the send fails |
| `unmatchable-condition` | error | A regex that does not compile, or a comma-broken `matches_any_of`; both route everyone to noMatch |
| `credentials` | error | A definition is meant to be committed |
| `split-without-nomatch` | warning | An unexpected answer has nowhere to go |
| `no-encryption` | warning | Publishing identifiers to Sheets in clear |
| `unpaired-answers` | warning | A blank cell cannot be read as timed-out vs not-asked vs failed |

`opening-cannot-open-session` needs to know what each content template actually
is, which costs one Content API call per template. It runs when you check a
single flow or deploy one, and is skipped on a whole-account sweep rather than
guessed at.

### What it found on this account

Running it over all 47 flows: **37 clean, 10 with errors.**

Seven flows share one identical defect - `BSC_baseline`, `BSC_endline`,
`BSC_screening`, `FMI_scheduling`, `FMI_screening_bsc`, `FMI_screening_bsv`,
`FMI_screening_elic`:

```
verif_1_pilot      --timeout--> verif_1_rem1_pilot
verif_1_rem1_pilot --timeout--> verif_1_rem3
verif_1_rem3       --timeout--> welcome_piloto_scr
(and the matching deliveryFailure transitions)
```

That is one bug, copy-pasted six times when the flows were duplicated. Six of
the seven are published. Anyone who stopped replying during pilot verification
is absent from the data entirely - not a `no_reply` row, no row.

Three more (`RST2023_innovationfair`, `ETPV Rifa`, `Te cuidadores`) publish rows
with no final-status variable at all, so those datasets cannot distinguish a
completion from a break-off.

**This is the argument for running the check at all.** None of it was visible in
the Studio editor, and the flows had been running for years.

### Locating the drop-off

Section-level status flags are the baseline and what the corpus uses:
`set_multierror_dem`, `set_no_reply_dem`, `set_fail_dem` per questionnaire
section. They tell an analyst which section a respondent died in.

For finer resolution, pair each answer with its own status column
(`q3` next to `q3_status`), which is what `unpaired-answers` looks for. It costs
a `set-variables` widget per question per break-off path, so it is a real
investment in an 85-question survey - 94% of answer columns on this account are
currently unpaired. Section-level is a legitimate choice; the point is to make
it deliberate, and to know that a blank answer cell alone is ambiguous between
timed-out, not-asked and delivery-failed.

The cheap third option: the Studio Executions API records the actual step
sequence per respondent, so the drop-off widget can be derived after the fact
with `rtt fetch` and no flow changes at all. It is not live in the dashboard,
but it costs nothing to add.

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
