---
name: whatsapp-template
description: Use when writing, reviewing, or submitting a WhatsApp message template for an IPA Twilio Studio survey - anything that has to reach a respondent before they have replied, or after the 24-hour window has closed. Runs a design discussion (purpose, category, language, content type, variables, research-ethics framing) before drafting, because a submitted template can never be edited. Knows this repo's `just template-*` recipes and src/requests_to_twilio/templates.py.
---

# WhatsApp templates for IPA surveys

## Why this needs care

Two facts drive everything here.

**A submitted template can never be edited.** Meta has no update operation.
Changing one word means a new template, a new name, and another review cycle.
There is exactly one chance to get the wording right, and it is before
submission.

**Every message sent before the respondent replies must come from an approved
template.** That is what "business-initiated" means. Since 1 April 2025,
sending template text in `Body` outside the 24-hour window fails outright with
error 63016 - it must be sent by `ContentSid`. Flows written before that date
break on relaunch for this reason and no other.

And one that is specific to IPA: these messages go to **research participants**,
not customers. An unsolicited WhatsApp message from an unrecognised number is
the classic shape of a scam. If a respondent does not immediately understand
who is writing and why, some fraction will report it as spam - and Meta pauses a
reported template for 3 hours, then 6, then deactivates it permanently. Losing
an approved template mid-round is an operational failure, not just an
annoyance.

## Step 0 - the bookend rule

**IPA surveys get a template at the beginning and a template at the end.**
Treat that as the default and deviate only with a reason.

```
opening template   ->  respondent replies / taps a button
                   ->  24-hour customer service window opens
                   ->  the whole questionnaire runs free-form, no approval
                   ->  closing template
```

**Why the opening one:** it is business-initiated. There is no alternative.

**Why the closing one, even though it may look unnecessary:** the closing
message fires *after* the respondent has finished answering, which can be hours
or days after they started. A participant who begins on Monday evening and
finishes Wednesday is outside the window, and a free-form close silently fails
with 63016 - they answer every question and hear nothing back. Sending the close
as a template makes it deliver regardless of timing. The cost is one extra
approval; the benefit is that the last thing a participant experiences is not
silence.

**What everything in between does not need:** the questionnaire itself runs
inside the window and needs no approval at all. A 50-widget survey needs two
templates, not fifty. Say this early - people routinely assume every question
needs review and brace for a week of waiting.

**Where a third is usually needed:** reminders to non-responders. Those fire
days later, well outside the window, so each reminder wave needs its own
template.

## Read the house style first

**`references/ipa-examples.md`** holds real approved copy from this account -
openings, closings, and the categories Meta actually assigned them. Read it
before drafting. IPA already has a house voice, the closing formula is settled
(`_Equipo IPA_`), and the UTILITY/MARKETING split is visible in the data rather
than theoretical.

Pull the live set any time:

```bash
just template-list --filter intro
just template-list --filter end
```

## Step 1 - have the discussion

Use `AskUserQuestion`. Do not skip to drafting; the answers change the copy,
not just the metadata. Bundle into at most two rounds.

**Round 1 - what and to whom**

1. **Which bookend is this?** Opening / closing / reminder. Or all of them, in
   which case draft the set together so the voice matches.
2. **What is the study, and how were these participants enrolled?** The opening
   template has to reference something the respondent recognises - a training
   they attended, a survey they consented to, an organisation they have met.
3. **Has the respondent consented to WhatsApp contact, and where?** If consent
   came through a different channel - an in-person baseline, a phone screening -
   say so in the copy. It helps approval and it is honest.
4. **Language(s).** Each is a separate template and a separate review.

**Round 2 - shape**

5. **Content type.** Text, quick-reply buttons, media header, list. The choice
   constrains where the template can be used.
6. **What varies per respondent.** Name, session, date, amount. Every variable
   needs a sample value or Meta rejects the template.
7. **Is there an incentive or compensation?** This is a category trap - see
   below.

Also establish, without asking formally if it is obvious:

- **Is this a test?** Test templates land on the real WhatsApp Business Account
  permanently, count against the 6,000-translation limit, and cannot be cleaned
  up from Meta's side easily. Name them recognisably, or accept that the test
  template becomes the production one.
- **Does the survey promise confidentiality?** If so, verify the flow actually
  encrypts before publishing to Sheets. The 2023 RST flow promised "all answers
  are strictly confidential" and wrote free-text straight to a shared
  spreadsheet. A promise the pipeline does not keep is worse than no promise.

## Step 2 - IPA-specific content rules

**Identify IPA and the study in the opening message.** Not just "Hi, welcome to
the survey". A participant should be able to tell in one line that this is the
organisation they already dealt with. Anonymity is what gets templates reported.

**Never put PII in a template body.** Templates are account-wide, permanent, and
readable by anyone with Console access. Names go in `variables` at send time,
never in the stored text.

**Do not make claims the pipeline cannot keep.** "Confidential", "anonymous",
and "only the research team will see this" are commitments. Anonymous in
particular is usually false - responses are tied to a phone number.

**Give a way out.** Participation is voluntary; the copy should not read as
obligatory. A respondent who feels trapped reports the template.

**Match the language to enrolment.** Someone recruited in Spanish should not
receive an English opt-in. Where a study spans languages, every language needs
its own approved template - budget the review time.

**Follow IRB-approved wording where it exists.** If the protocol specifies
consent or contact language, that text wins over anything more natural-sounding.
Ask rather than paraphrase.

**The incentive trap - confirmed in this account's own data.** Every approved
opening that leads with compensation landed in MARKETING; every one that leads
with the consent basis landed in UTILITY. Compare:

> **MARKETING** - "Luego de completar esta encuesta, **le daremos un bono de
> regalo por COP 10.000**... **¿Se anima?**" (`edu_baseline_welcome2`)

> **UTILITY** - "Esta es una encuesta de actualización de datos de 5 minutos,
> **para la cual nos autorizaste un nuevo contacto**."
> (`beat_intro2_recontacto`)

Incentives can still be mentioned - state them as a factual consequence, late in
the message, not as the reason to act. Note that `intro_fim2_1` *does* reference
the prior survey but bolds the incentive, and still landed in MARKETING:
emphasis and position matter, not just presence.

**Name the consent basis.** This is the single highest-leverage sentence, and
the one people skip. "para la cual nos autorizaste un nuevo contacto",
"usted fue formado por NRC... y autorizó ser contactado(a) a través de este
medio". It earns UTILITY and it is simply true.

## Step 3 - choose the category deliberately

| Category | Use for | Consequence |
| --- | --- | --- |
| `UTILITY` | Something the recipient already signed up for: session materials, feedback on training they attended, follow-up to a survey they consented to | Not subject to marketing limits. Delivers reliably. |
| `MARKETING` | Promotion, invitation to something new, anything with a persuasive frame | Per-user marketing limits, easier opt-out, worse delivery |
| `AUTHENTICATION` | One-time passcodes only | Strict format rules |

**IPA research surveys are almost always UTILITY** - the respondent enrolled in
the study or attended the training. But Meta assigns the final category by
reading the copy, and promotional framing pulls it toward MARKETING.

**Always request UTILITY explicitly.** Meta does not reliably override the
requested category. This account holds two near-identical closings -
`end1_msj` ("Muchas gracias por su atención...") is UTILITY, while
`end_capacitacion` ("¡Muchas gracias por tu tiempo!") is MARKETING. Neither is
promotional; the difference is what was asked for at submission. `--category`
is not a hint.

Worked example from this account. The 2023 template was approved as MARKETING:

> Glad to have you as part of the 2023 India Research Staff Training! I'm here
> to give you a **sneak peek into something special** coming up later this
> week... **Discover more about the vibrant destination of Goa.**

The 2026 rewrite reads as UTILITY:

> Hi {{1}}, you are registered for the 2026 India Research Staff Training. We
> will use WhatsApp to share session materials and to collect your feedback
> during the training.

Same information, no persuasion. Plain statements of fact about something the
person already agreed to. That is the lever.

## Step 4 - languages

Each language is a **separate template with its own approval**. There is no
multi-language template.

Ask explicitly, and be precise about the locale code:

- `en` vs `en_US` vs `en_GB` are distinct templates. This account already has
  RST templates split across `en_US` and `en_GB` for no apparent reason, which
  is now permanent clutter.
- **Pick one and use it consistently.** Prefer the bare code (`en`, `es`) unless
  there is a real regional difference in the copy.
- For India, English is usually right, but ask - Hindi or a regional language
  may be expected, and that doubles the review cycles.

Check what already exists before creating anything:

```bash
just template-list --filter rst
```

An approved template with year-agnostic wording can often be reused rather than
recreated. Approvals do not expire; they only lapse if Meta pauses the template
for negative user feedback.

## Step 5 - choose the content type

| Type | Business-initiated? | Notes |
| --- | --- | --- |
| `twilio/text` | Yes, with approval | Simplest, most likely to be approved. Good default for closing messages |
| `twilio/quick-reply` | Yes, with approval | Up to 10 buttons; only 3 if used unapproved in-session. Titles max 20 chars. Good default for opening messages |
| `twilio/media` | Yes, with approval | Media type frozen at approval - a template approved with an image can never send a video |
| `twilio/card` | Yes, with approval | All buttons must be the same action type |
| `twilio/list-picker` | **No** | Cannot start a conversation |
| `twilio/location` | **No** | Cannot start a conversation |

For an opening template, `twilio/quick-reply` with a single Start button is
usually best: one tap opens the 24-hour window, and the payload is readable in
Studio via `widgets.<name>.inbound.ButtonPayload` for a Split widget.

For a closing template, plain `twilio/text` is usually right - there is nothing
left to ask. Follow the house formula: thank, close warmly, sign off as
`_Equipo IPA_` (adding the partner where there is one). If nobody monitors the
number after the round, say so, as `rifa_wa_cierre` does - "te pedimos por favor
no responder a este chat". A number that silently swallows replies is a poor
last impression.

**Keep button IDs stable across years.** If the 2023 flow branched on
`Start_payload`, reuse that exact ID and the existing Split widgets keep working
without rewiring.

## Step 6 - draft into a file, never straight to the API

Definitions live in `templates/<name>.json` so the exact wording sent to
respondents is version-controlled and reviewable in a diff. Given immutability,
the diff is the only safety net.

```json
{
  "_comment": [
    "Why this template exists, what it replaces, and any decision worth",
    "remembering. Stripped before the payload is sent."
  ],
  "friendly_name": "rst2026_wa_session_intro",
  "language": "en",
  "variables": { "1": "Priya" },
  "types": {
    "twilio/quick-reply": {
      "body": "Hi {{1}}, you are registered for the 2026 India Research Staff Training.\n\nWe will use WhatsApp to share session materials and to collect your feedback during the training.\n\nTo start receiving them, press the button below.",
      "actions": [{ "title": "Start", "id": "Start_payload" }]
    }
  }
}
```

Naming: `<project><year>_<purpose>`, lowercase with underscores - Meta requires
that for the stored name, and the year makes it obvious later which round a
template belongs to. Use a consistent suffix for the bookends, e.g. `_intro`
and `_close`.

## Step 7 - what gets templates rejected

`check_variables()` in `src/requests_to_twilio/templates.py` warns about the
first three automatically. Check the rest by reading.

- **A variable with no sample.** Every `{{1}}` needs an entry in `variables`.
- **Body starts or ends with a variable.** `"Hi {{1}}"` as the entire opening is
  high-risk; put words around it.
- **Button title over 20 characters.** WhatsApp truncates silently.
- **Two adjacent variables** - `{{1}} {{2}}` reads as spam.
- **Promotional framing in a UTILITY submission** - Meta re-categorises rather
  than rejecting, which is worse because it is silent.
- **URLs with no context.** Templates support links, but a bare link looks like
  phishing, and WhatsApp does not render URL previews in templates.
- **Claims the flow cannot keep.**

## Step 8 - create, review, submit

Two separate steps, deliberately. `create` is reversible; `submit` is not.

```bash
# 1. Create in Twilio only. Not yet sent to Meta - still deletable.
just template-create templates/rst2026_wa_session_intro.json

# 2. Read the printed copy one more time. This is the last chance.

# 3. Submit to Meta. Irreversible.
just template-submit rst2026_wa_session_intro --category UTILITY

# 4. Approval usually lands within minutes.
just template-status rst2026_wa_session_intro
```

Submit the bookends together so the round is not blocked half-approved.

**Never run `template-create --submit` on a first draft.** The combined flag is
for wording that has already been reviewed.

**Never submit on the user's behalf without explicit confirmation in that
turn.** Both commands prompt, but do not answer the prompt for them - the
decision is theirs and it is permanent.

If rejected, `just template-status` shows Meta's reason. The template cannot be
fixed; write a new definition under a new name, and record what was rejected in
the `_comment` of the replacement so the mistake is not repeated.

## Step 9 - wire it into the flow

Once approved, the template is used by its `HX` SID:

1. In Studio, open the widget that sends the message - the first widget for the
   opening template, the final one for the close.
2. Set **Message Type** to *Content template*.
3. Paste the `HX` SID; fill in variables.
4. Under Advanced Configuration set **Send message from** to the WhatsApp
   sender.

Then re-pull the flow so the repo reflects what is deployed:

```bash
just flow-pull <flow-name>
```

## The Python underneath

`src/requests_to_twilio/templates.py`, if the recipes are not enough:

| Function | Does |
| --- | --- |
| `load_definition(path)` | Reads a definition, strips `_comment`, validates required fields |
| `check_variables(definition)` | Returns warnings for the common rejection causes |
| `find_by_name(client, name)` | Resolves a friendly name to a content resource |
| `create(client, definition)` | Creates in Twilio. Does not submit |
| `submit(client, sid, name, category)` | Submits to Meta. Irreversible |
| `approval_status(client, sid)` | Status, category, rejection reason |
| `list_templates(client, filter)` | All templates with approval status |

The SDK's `ContentCreateRequest` calls `.to_dict()` on whatever it gets for
`types`, so a bare dict raises `AttributeError`. `_RawTypes` wraps it. If you
write new code against the Content API directly, that is the trap.

Categories are `UTILITY`, `MARKETING`, `AUTHENTICATION` - the `CATEGORIES`
constant.

## Checklist before submitting

- [ ] Both bookends drafted - opening *and* closing - plus any reminder waves
- [ ] Does an approved template already exist that could be reused?
- [ ] Opening message identifies IPA and the study the participant recognises
- [ ] Consent basis referenced where contact was agreed elsewhere
- [ ] Category chosen deliberately, and the copy actually reads that way
- [ ] Any incentive framed as consequence, not inducement
- [ ] Language code consistent with the account, and matches enrolment language
- [ ] Every variable has a sample; body does not start or end with one
- [ ] Button titles under 20 characters; IDs match existing flow branching
- [ ] No PII in the body
- [ ] Any confidentiality claim is backed by encryption in the flow
- [ ] IRB-approved wording used where the protocol specifies it
- [ ] The definition is committed, so the wording is reviewable later
- [ ] The user has explicitly confirmed submission in this turn
