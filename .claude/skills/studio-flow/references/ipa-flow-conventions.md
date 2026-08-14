# IPA Studio flow conventions, measured

Derived from all 47 Studio flows on the `IPA_Console_3` account (5.3 MB of
definitions, 2021-2026). This is what the team actually does, not what a Twilio
tutorial suggests. Re-derive it any time by pulling the corpus:

```bash
just flow-list
just flow-pull <name>          # into flows/, which is gitignored
```

## Scale

| | |
| --- | --- |
| Flows | 47 |
| Largest | `BSC_endline_v2`, 635 widgets |
| Median | 41 widgets |
| Highest revision | `FIM_followup`, rev 2613 |

Revision counts in the hundreds and thousands are normal, and none of those
edits were reviewable before this repo started pulling definitions.

## Widget mix across the corpus

| Count | Type | What it is doing |
| --- | --- | --- |
| 1391 | `run-function` | Waits, encryption, publishing |
| 1259 | `split-based-on` | Answer validation and error branching |
| 1059 | `send-message` | Statements, error messages, closings |
| 867 | `set-variables` | Paradata: errors, timestamps, status flags |
| 723 | `send-and-wait-for-reply` | The actual questions |
| 4 | `make-http-request` | Rare |
| 4 | `run-subflow` | Rare |

**There are roughly twice as many paradata and control widgets as questions.**
A 723-question corpus carries 867 `set-variables` and 1259 splits. That ratio is
the survey discipline: every question is wrapped in validation, error counting
and status tracking.

## Transition events

| Count | Event | Note |
| --- | --- | --- |
| 2852 | `match` | Split branches |
| 1259 | `noMatch` | **Every split has one** |
| 723 | `timeout` | **One per question** |
| 723 | `deliveryFailure` | **One per question** |
| 770 | `incomingMessage` | |

`timeout` and `deliveryFailure` appear exactly once per
`send-and-wait-for-reply`. Every question handles non-response and delivery
failure explicitly. Never leave either unwired.

## Paradata variable vocabulary

The `set-variables` widgets across the corpus, most-used first:

| Variable | Uses | Meaning |
| --- | --- | --- |
| `set_complete` | 15 | Respondent finished |
| `set_fail` | 14 | Delivery failed |
| `set_no_reply` | 12 | Timed out |
| `set_multierror` | 11 | Too many invalid answers |
| `set_survey_fail` | 9 | Failure in the survey section |
| `set_initial_no_reply` | 8 | Timed out at the opening |
| `counter_error_<q>` | 7+ | Per-question invalid-answer counter |
| `set_time_start` / `set_time_fin` | 5-6 | Duration measurement |
| `set_consent` | 5 | Consent recorded |

**Section-scoped naming.** The larger flows suffix by questionnaire section
rather than reusing one global flag. `edutainment_bl` carries the full set for
each of five sections:

```
set_multierror_dem   set_fail_dem   set_no_reply_dem      # demographics
set_multierror_obs   set_fail_obs   set_no_reply_obs      # observation
set_multierror_mig   set_fail_mig   set_no_reply_mig      # migration
set_multierror_emp   set_fail_emp   set_no_reply_emp      # employment
set_multierror_social set_fail_social set_no_reply_social # social
```

That is what makes it possible to say *where* a respondent broke off, not just
that they did. Copy this pattern.

Prefixes also mark the stage: `set_intro_*`, `set_survey_*`, `set_reminder_*`,
`set_initial_*`, `set_verif_*`.

## The error-counter pattern

Per question, three widgets:

```
question  --(reply)--> split_<q>        validate the answer
                        |- match     -> next question
                        |- noMatch   -> set_counter_error_<q>  (increment)
                                        |
                                        v
                                     split_error_<q>
                                        |- under limit -> error_message_<q> -> re-ask
                                        |- over limit  -> set_multierror_<section>
                                                          -> exit that section
```

From `edutainment_bl`:

```
[split_error_1] tests {{flow.variables.counter_error_1}}
    less_than  -> error_message_1     (re-ask)
    noMatch    -> set_multierror_dem  (give up on this section)
```

**This is the stop rule.** A respondent who cannot give a valid answer is not
asked forever; after N attempts the section is marked `multierror` and the flow
moves on. The flag is published, so the analyst can see the question was
abandoned rather than skipped.

## Timeouts on `send-and-wait-for-reply`

| Seconds | Hours | Uses |
| --- | --- | --- |
| 14400 | 4h | 230 |
| 3600 | 1h | 203 |
| 18000 | 5h | 67 |
| 10800 | 3h | 64 |
| 7200 | 2h | 45 |
| 86400 | 24h | 40 |

**4 hours and 1 hour are the house defaults.** Long timeouts suit WhatsApp -
respondents answer around their day, not in one sitting. But note the
interaction with the 24-hour session window: a chain of 4-hour timeouts can push
the closing message outside it, which is why the close is a template.

## What gets encrypted

Encrypt-widget parameters across the corpus:

| Parameter | Uses |
| --- | --- |
| `name` | 15 |
| `p_number_original` | 12 |
| `Number` | 7 |
| `verif_dob_1`, `verif_dob_2`, `year`, `month` | 4-6 each |
| `phone_2_1`, `num_tel_contacto`, `num_wa`, `phone_fam` | 2-3 each |
| `address`, `localidad` | 2 each |

**Direct identifiers and date of birth are encrypted. Analysis variables are
not.** `edutainment_bl` publishes `age`, `gender`, `education`,
`ciudad_principal` and `assigned_group` in clear, while the phone number goes
through the encrypt widget and lands as `enc_p_number_original`.

That split is correct and deliberate: encrypting the analysis variables would
make the Sheet useless for monitoring without buying much, since the identifiers
are what re-identify a respondent.

**Naming convention: `enc_` prefix** on the published column.

```
enc_p_number_original = {{widgets.function_encrypt.parsed.enc_p_number_original}}
```

## The publish payload

From `edutainment_bl`, the shape to copy:

```
caseid                   {{flow.data.caseid}}              # launch data
age, gender, education   {{flow.data.*}}                   # analysis vars, clear
enc_p_number_original    {{widgets.function_encrypt.parsed...}}   # PII, encrypted
set_multierror_dem       {{flow.variables.*}}              # paradata per section
set_fail_dem             {{flow.variables.*}}
set_no_reply_dem         {{flow.variables.*}}
...                                                        # x5 sections
set_time_first_message   {{flow.variables.*}}              # timestamps
set_time_start           {{flow.variables.*}}
```

Four groups: launch data, analysis variables in clear, encrypted identifiers,
paradata and timestamps. The paradata is roughly half the columns.

## Widget naming

Bilingual, and inconsistent across eras. Both appear:

| Purpose | Names in use |
| --- | --- |
| Encryption | `encriptador` (16), `function_encrypt` (5) |
| Publish | `publish_gsheets` (19), `publish` (5) |
| Wait/delay | `espera_*` (Spanish), `wait_*`, `function_wait*` |
| Error wait | `espera_multierror`, `espera_multierror_1` |

For new flows prefer the English forms - `function_encrypt`, `publish_gsheets`,
`wait_*` - which is what the newer flows use, and which `rtt flow pull` looks
for when it checks whether a flow encrypts.

## Consent

22 of 47 flows carry consent language. The pattern:

1. Intro identifies IPA, the study, and the consent basis
2. A `pre_consent` message links the full informed-consent document
   (`https://bit.ly/consent_ipa_...`)
3. A yes/no question records the decision into `set_consent`
4. Declining routes to a polite close, not silence

From `BSC_baseline`:

> Por último, *por favor tenga en cuenta que esta encuesta es sólo una parte de
> un estudio más grande*. En este podría recibir mensajes y otra encuesta. Para
> más información sobre este _consentimiento informado_ *ingrese al siguiente
> link*: https://bit.ly/consent_ipa_p_CFM_2023

From `BEAT_control_recontact`, the consent-basis sentence that also earns
UTILITY on the template:

> Nos estamos comunicando nuevamente para actualizar algunos datos, dado que
> **nos autorizaste un nuevo contacto**. La encuesta te tomará 5 minutos y
> estará disponible por las próximas 24 horas.

## Content templates inside flows

Only 8 of 47 flows reference an `HX` content SID in a widget, `edutainment_bl`
most heavily at 25 widgets. This is the newer practice and the one that survives
the April 2025 rule change. Older flows send plain bodies and will fail
business-initiated.

## Audit: flows that publish without encrypting

10 flows have a publish widget and no encryption widget:

```
Apapachar_bonos                published
BEAT_rifas_WA                  draft
BEAT_rifas_endline             published
ETPV Rifa                      published
Mujeres360_post_facilitadoras  draft
RST2023_WA_session_1           draft
RST2023_india_feedback         draft
RST2023_innovationfair         published
Te cuidadores                  published
extortion_survey               published
```

Some are legitimately identifier-free. **`extortion_survey` deserves a look** -
a survey on that topic, published to a shared Google Sheet with no encryption
widget, is the highest-risk item the corpus surfaced.
