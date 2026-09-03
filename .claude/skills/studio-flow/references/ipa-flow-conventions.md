# A widget library for survey flows

How IPA builds a questionnaire in Twilio Studio. This is a library of shapes
that have run rather than a tutorial: if you are turning a paper instrument into
a WhatsApp flow, the subgraph below is the unit you are working in.

The thing to internalise first is that **a question is not a widget.** On paper
a question is one row. In Studio it is a small subgraph of six to nine widgets,
and the extra ones are not ceremony — each exists because something in a
messaging channel can fail in a way a paper form cannot: the answer can be
unusable, it can never arrive, or it can arrive after the conversation has
closed. Expect roughly twice as many control and paradata widgets as question
widgets in a finished flow.

Pull any flow to read its real shape:

```bash
just flow-list
just flow-pull <name>          # writes flows/<name>.json, gitignored
just flow-check <name>         # what it gets wrong
```

## One question, nine widgets

This is the full form, for a question whose answer has to satisfy a constraint —
a number in a range, one of a fixed set of codes, a date. Names follow the
question's own id (`P3` here), which is what makes a 60-widget canvas navigable.

```text
P3                    send-and-wait-for-reply
   |- incomingMessage -> stopcheck_P3
   |- timeout         -> mark_no_reply          (never answered)
   |- deliveryFailure -> mark_delivery_failed   (never arrived)

stopcheck_P3          split-based-on   {{widgets.P3.inbound.Body}}
   |- match (stop|quit|cancel) -> mark_optout
   |- noMatch                  -> split_P3

split_P3              split-based-on   {{widgets.P3.inbound.Body}}   <- the constraint
   |- match   (a valid answer) -> store_P3
   |- noMatch                  -> retry_P3

store_P3              set-variables    -> next question               <- the flag
retry_P3              set-variables    -> check_P3                    <- the counter
check_P3              split-based-on   {{flow.variables.tries_P3}}    <- the stop rule
   |- match (under the limit)  -> error_P3
   |- noMatch (limit reached)  -> giveup_P3

error_P3              send-message     -> P3        (say what was wrong, re-ask)
giveup_P3             set-variables    -> next question
```

Reading it as the seven jobs it does:

| Job | Widget | Why it cannot be skipped |
| --- | --- | --- |
| Ask | `P3` | — |
| Let them leave | `stopcheck_P3` | inside a session a "STOP" is an ordinary inbound message; nothing honours it unless the flow looks |
| Constrain | `split_P3` conditions | the constraint is a *condition*, evaluated by Studio, not a note to the respondent |
| Branch on validity | `split_P3` | `noMatch` is the whole point: an unexpected reply must go somewhere |
| Count attempts | `retry_P3` + `check_P3` | without a limit a respondent who cannot answer is asked forever |
| Record the answer | `store_P3` | — |
| Handle absence | `mark_no_reply`, `giveup_P3` | silence and exhausted-retries are different outcomes and must not share a column |

**`timeout` and `deliveryFailure` belong on every `send-and-wait-for-reply`,
exactly once each.** They are the two ways a question can fail without the
respondent doing anything. Leaving either unwired produces an execution that
simply stops, and a person who is then absent from the data rather than recorded
as unreachable.

## One question, four widgets

Open text needs no constraint, so the middle of the subgraph collapses:

```text
P1  send-and-wait-for-reply
      |- incomingMessage -> stopcheck_P1 -> store_P1 -> next question
      |- timeout         -> mark_no_reply
      |- deliveryFailure -> mark_delivery_failed
```

Worth knowing what you are trading. An open question always "validates", so
there is no retry, no error message and no give-up path — and also no usable
codes. Free text arrives as `Thursfay`, `NO`, `R and surveycto`. That is the
argument for constraining answers wherever the paper instrument would have used
a code list, not an argument for skipping the constraint.

## The stop rule

The counter is the part people leave out, and it is the part that keeps a
respondent from being trapped.

```text
[check_P3] tests {{flow.variables.tries_P3}}
    is 1, is 2   -> error_P3        (re-ask, with a message saying what to send)
    noMatch      -> giveup_P3       (abandon this question, or this section)
```

After N attempts the question is abandoned and the flow moves on. Two rules
about that:

- **The give-up is flagged and published.** An abandoned question and a skipped
  question look identical in the data unless something says which happened.
- **The re-ask says what was wrong.** `error_P3` is a `send-message`, not a bare
  loop back to the question. Re-sending the same words to somebody who has
  already failed to answer them is how a respondent stops replying.

For a long instrument, scope the give-up to a **section** rather than a single
question: `set_multierror_dem` abandons demographics and carries on. Somebody
stuck on one item should not lose the rest of the questionnaire.

## Paradata naming

Suffix by questionnaire section rather than reusing one global flag, so the data
can say *where* a respondent broke off rather than only that they did:

```text
set_multierror_dem   set_fail_dem   set_no_reply_dem      # demographics
set_multierror_obs   set_fail_obs   set_no_reply_obs      # observation
set_multierror_mig   set_fail_mig   set_no_reply_mig      # migration
set_multierror_emp   set_fail_emp   set_no_reply_emp      # employment
```

Prefixes mark the stage: `set_intro_*`, `set_survey_*`, `set_reminder_*`,
`set_verif_*`.

**These flags are `1` or blank, never `0`.** So "not complete" is encoded as
absence, which is indistinguishable from a column the publish step dropped. Do
not build an analysis on them alone — derive a single `final_status` at the
widget every terminal path converges on, and publish that too. See
`requests_to_twilio.outcomes.final_status_liquid`.

## Widget naming

Prefer the English forms for new flows — `function_encrypt`, `publish_gsheets`,
`wait_*`. Older flows use Spanish equivalents (`encriptador`, `espera_*`) and
both are in circulation, but `rtt flow check` looks for the English ones when it
decides whether a flow encrypts, so a Spanish name reads as *no encryption*.

## Timeouts

**4 hours and 1 hour are the house defaults.** Long timeouts suit WhatsApp:
respondents answer around their day, not in one sitting. 24 hours appears where
a round is genuinely meant to span one.

Note the interaction with the session window. A chain of 4-hour timeouts can
push the closing message past 24 hours from the last inbound, at which point a
free-form message is refused — which is why the close is a template.

## What gets encrypted

**Direct identifiers and dates of birth. Not analysis variables.** A typical
flow encrypts `name`, the original phone number, contact numbers, address and
the date-of-birth parts, while publishing `age`, `gender`, `education`, city and
assigned group in clear.

That split is deliberate rather than lazy: encrypting the analysis variables
would make the sheet useless as a monitoring dashboard without buying much,
because the identifiers are what re-identify a respondent.

Convention: **`enc_` prefix** on the published column.

```text
enc_p_number_original = {{widgets.function_encrypt.parsed.enc_p_number_original}}
```

The encrypt widget goes immediately before the publish widget. Anything between
them is a chance for the plaintext to reach the destination.

## The publish payload

Four groups, in this order:

```text
caseid                   {{flow.data.caseid}}            # launch data
age, gender, education   {{flow.data.*}}                 # analysis vars, in clear
enc_p_number_original    {{widgets.function_encrypt...}}  # identifiers, encrypted
set_multierror_dem       {{flow.variables.*}}            # paradata, per section
set_fail_dem             {{flow.variables.*}}
set_no_reply_dem         {{flow.variables.*}}
final_status             {{flow.variables.final_status}}  # the derived rollup
set_time_first_message   {{flow.variables.*}}            # timestamps
```

Paradata is routinely half the columns. That is the right proportion, not
bloat — it is the difference between a dataset that can report a response rate
and one that cannot.

**One publish widget, and every terminal path reaches it.** A break-off that
ends without publishing produces no row, and a respondent with no row is
indistinguishable from somebody never contacted.

## Consent

1. The opening identifies the organisation, the study, and the consent basis —
   why this person is being contacted at all.
2. A message links the full informed-consent document.
3. A yes/no question records the decision into `set_consent`.
4. Declining routes to a polite close, not to silence.

The consent basis is worth stating plainly, and it does double duty: naming why
you are permitted to make contact ("you agreed to be contacted again") is also
what tends to earn a UTILITY category on the opening template, rather than
MARKETING with its per-user limits.

Consent belongs **in session**, not in a template. The reply to the opener opens
the 24-hour window, and everything after it — consent included — is free-form
and needs no approval.

## The template set a flow actually needs

A business-initiated message must be an approved template; anything inside the
24-hour window the respondent's reply opens is free-form. So the approval list
is short, and it is always roughly this:

| Template | Why it must be approved | Content type |
| --- | --- | --- |
| **The opener, with a Start button** | nobody has replied yet, so there is no session | `quick-reply` |
| **A media header**, if the study shows an image or a document | same reason, when the opener carries media | media header |
| **The close, for people who never replied** | their window never opened, so a free-form message is refused | `text` |

Everything else — consent, every question, every retry, every error message —
goes out in session and needs no approval at all. On a six-question instrument
that is two approved templates against a dozen free messages.

Three things follow, and each has cost a round somewhere:

- **The Start button is mechanical, not decorative.** The opener is
  send-and-wait-for-reply, and tapping Start is what opens the window. Without a
  reply there is no session and every later message fails.
- **The close is a template because of the timeout maths.** A chain of 4-hour
  timeouts can push the ending past 24 hours from the last inbound. Somebody who
  answered everything gets the close in session; somebody who never replied
  needs the approved version.
- **Media must be fetchable by Meta, from outside, at submission time.** A
  Twilio asset set to `private`, an expired link, anything behind SSO — all fail
  before a human reviews a word. Open the URL in a signed-out window first.

An older flow that sends plain bodies business-initiated will fail. That is the
April 2025 rule change, and it is why the newer practice is to reference a
content SID from the widget rather than typing a body into it.
