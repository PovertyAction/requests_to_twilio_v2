# Designing your own instrument

**To write a survey, start at [docs/writing-a-survey.md](writing-a-survey.md).**
You describe the instrument as spreadsheet rows and the toolkit generates the
flow; you do not edit Studio, and you do not copy an existing survey. This page
is the reasoning *behind* that format — what to know before you design the
questions themselves.

Read it if you are choosing between a list and open text, deciding how many
options an item should have, working out what to encrypt, or wondering why the
conventions here differ from the flows already on our account.

The rules below are IPA survey-research conventions, not generic Twilio chatbot
practice, and several of them are deliberate departures from what the flows on
our own account already do. Where that is true it is called out, because
otherwise the next person "fixes" them back by copying an older flow.

## The shape of a survey flow

A chatbot answers whoever turns up. A survey instrument has a sampling frame, and
that difference drives most of the structure:

```text
Trigger
  incomingRequest ──▶ opener (approved template)
  incomingMessage ──▶ acknowledge once, end        ← someone writing in cold
                        ↓
                     consent gate
                        ↓
                     questions
                        ↓
                     mark_* (set the outcome)
                        ↓
                     finish ──▶ encrypt ──▶ publish ──▶ closing message
```

Two things about that graph carry most of the weight.

**Every terminal path converges on `finish`.** Complete, declined, timed out,
undeliverable — all of them route through the encrypt-then-publish pair, so a row
exists whatever happened. A path that skips it produces no row at all, and a
respondent who broke off becomes indistinguishable from one who was never
contacted. That is the difference between measured attrition and missing data,
and `just flow-check` reports any path that misses it.

**The trigger routes two events, not one.** `rtt launch` creates executions over
the REST API, which fires `incomingRequest`. A flow wired only for
`incomingMessage` ends at the trigger having sent nothing — while the launcher
reports every send successful.

## Paradata is about half the columns, and that is correct

Across the flows on our account there are roughly two control widgets per
question: a `set-variables` recording what happened, and a split deciding where
to go. Publish them. A published answer with no status beside it means a blank
cell cannot be read as *timed out* versus *not asked* versus *refused*.

Minimum published set:

- one column per question, plus a `_status` beside it
- an `outcome`: `complete`, `declined`, `incomplete`, `unreachable`, `undeliverable`
- `caseid`, so the row joins back to the sampling frame
- `execution_sid`, so it joins back to the platform's own record
- `sent_at` — supplied by the launcher, because **Studio cannot produce a UTC
  timestamp**. It has no `now` variable, the Liquid date filter has no timezone
  directive, and `%s` writes the literal string `%s` into the column. Both ends
  of a respondent's participation come from sources that are UTC by construction.

## Asking a question

**Use a list picker by default.** House practice on our own account is the
opposite — 373 of 400 questions are plain text bodies — so this is a departure,
and the argument for it is that a tap cannot be misspelled, mis-cased, or
answered in a way the split did not anticipate.

The limits that shape question design rather than just implementation:

| | Limit | Consequence |
| --- | --- | --- |
| List items | **10** | more than that is not a list question |
| List item text | **24 characters** | `Neither agree nor disagree` is 26. The neutral midpoint of a standard Likert does not fit, in English or Spanish |
| List body | 1024 characters | never come close; measured p90 is 356 |
| Quick-reply buttons in session | 3 | use a list instead; it needs no approval either |

An 0–10 NPS item is eleven points, so it does not fit a list picker at all.

**The two interactive types disagree about what a tap sends.** A tapped **list
row returns its `id`**; a tapped **quick-reply button returns its title**. The
documentation does not say so, and getting it wrong sends every tap to the retry
nudge while the respondent sees a survey that does not understand them. Accept
the id, the label, and — carefully — a typed position.

**Careful about typed positions.** On a scale whose labels are themselves numbers
(`0 projects / 1 project / 2-3 projects`), a typed `1` means the label to the
respondent and the position to the split, and they are different options. The
builder detects that and refuses bare digits on those questions, so the
respondent is asked again rather than silently miscoded.

**Use `regex`, not `matches_any_of`.** House practice is nine to one the other
way. The reason to depart: `matches_any_of` takes its alternatives as a single
comma-delimited string, so a comma inside an option label silently becomes two
alternatives that can never match. With regex a comma is just a character. Two
things to remember — Studio anchors the pattern to the whole string, so bare
alternation `a|b` can bind as `(^a)|(b$)`; wrap it as `(?:a|b)`. And a pattern
loose enough to match anything is worse than one that matches too little,
because junk gets stored as a real answer and the respondent is never asked
again.

## Consent, refusal, and stopping

**Consent goes in-session**, after the opener, as quick-reply buttons. Only the
opener is business-initiated and therefore in front of Meta, so the full IRB
wording never goes through template review and can change without a
resubmission.

**An unreadable reply is not a refusal.** Routing `noMatch` straight to
"declined" publishes "what is this?", a voice note and an emoji as explicit
refusals — and refusal rate is a headline number in a consent-based study. Give
consent at least one re-ask, and a third value for genuinely unclear. Note the
asymmetry that hides this: every ARM 2 question gets two retries and the single
most consequential question had none.

**Handle STOP.** Twilio's own opt-out handling covers the carrier keywords for
SMS, but mid-survey in WhatsApp a "STOP" arrives as an ordinary reply — stored as
the answer to whatever was asked. A respondent who types it and is then asked
three more questions is a research-ethics problem before it is a bug.

The demo flow puts a split ahead of every store widget, in both arms, routing to
`mark_optout` → `finish`, so a row still exists with `outcome=optout` and the
answers already given are kept. `flow-check` warns (`no-optout-path`) if a flow
never looks for a stop word — in any wording, since your list is your business.

**Retry limits are ethical, not just technical.** Re-asking someone who cannot
answer is badgering a volunteer. Two nudges, then move on.

## Timeouts and delivery failures

Every `send-and-wait-for-reply` needs both a `timeout` and a `deliveryFailure`
transition. `flow-check` errors without them.

They mean different things and deserve different outcomes:

- **timeout** — they were reachable and stopped replying. `incomplete`, and they
  can still receive a closing message: the window is open.
- **deliveryFailure** — the message never arrived. Sending another will fail the
  same way; publish the row and send nothing.
- **never answered the opener** — `unreachable`. The 24-hour window never opened,
  so any later message is business-initiated and fails with `63016`. Only an
  approved template can reach them.

## What to encrypt

Direct identifiers only: name, phone, address, date of birth. Encrypting `age` or
`gender` would break monitoring without protecting much, since the identifiers
are what re-identify someone.

Put the encryption widget immediately before publish, and pass every value that
needs protecting as a parameter. Every parameter is encrypted and returned under
the same key, so there is nothing to edit in the JavaScript when your questions
change.

Route the encryption widget's **failure** somewhere that says so. Publishing
straight from the failure branch writes empty identifier columns under
`outcome=complete`, which is indistinguishable from a respondent who had no name
in the sample. Read [encryption.md](encryption.md) before promising an IRB
anything.

## Before you deploy

```powershell
just flow-check flows/my_flow.json
just flow-schema flows/my_flow.json --table my_db.main.my_round
just flow-deploy flows/my_flow.json --publish
```

And then test it against a real number. Nine defects once survived a full test
suite, a passing `flow-check` and a clean dry run — every one of them found by
live traffic. Offline confidence is not evidence.

---

The agent-facing version of this material, with more worked examples, is in
`.claude/skills/studio-flow/`.
