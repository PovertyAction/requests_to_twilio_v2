---
name: whatsapp-template
description: Use when writing, reviewing, or submitting a WhatsApp message template for a Twilio Studio survey - anything that has to reach a respondent before they have replied. Runs a design discussion (purpose, category, language, content type, variables) before drafting, because a submitted template can never be edited. Knows this repo's `just template-*` recipes and src/requests_to_twilio/templates.py.
---

# WhatsApp templates for Twilio surveys

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

So the job is not "write a nice message". It is: decide the smallest set of
templates that will survive review and still work in a year.

## Step 0 - the question that saves the most work

**How many templates does this flow actually need?**

Usually one. The pattern:

```
approved template  ->  respondent taps a button or replies
                   ->  24-hour customer service window opens
                   ->  every later message can be free-form
```

Only the *first* message needs approval. A 50-widget survey needs one template,
not fifty. Before drafting anything, walk the flow and find the messages that
can be sent with no prior inbound message. Those, and only those, need
templates. Reminders sent days later are the usual second case, because the
window has closed by then.

Say this out loud to the user early. People routinely assume every question
needs approval and brace for a week of review.

## Step 1 - have the discussion

Use `AskUserQuestion`. Do not skip to drafting; the answers change the copy,
not just the metadata. Bundle into at most two rounds.

**Round 1 - what and to whom**

1. **Purpose of this message.** Opt-in invitation / reminder to a
   non-responder / notification of something they signed up for / re-contact
   after a long gap. This determines the category, which determines
   deliverability.
2. **Has the recipient agreed to be contacted on WhatsApp?** If consent came
   through a different channel, say so in the copy - it materially helps
   approval and it is honest.
3. **Language(s).** See the language section below; each one is a separate
   template and a separate review.

**Round 2 - shape**

4. **Content type.** Text, quick-reply buttons, media header, list. See the
   table below - the choice constrains where the template can be used.
5. **What varies per recipient.** Name, session, date. Every variable needs a
   sample value or Meta rejects the template.

Also establish, without asking a formal question if it is obvious from context:

- **Is this a test?** Test templates land on the real WhatsApp Business Account
  permanently, count against the 6,000-translation limit, and cannot be
  cleaned up from Meta's side easily. Name them so they are recognisable, or
  accept that the test template becomes the production one.
- **Does the survey text promise confidentiality?** If so, check that the flow
  actually encrypts before publishing to Sheets. A promise the pipeline does
  not keep is worse than no promise.

## Step 2 - choose the category deliberately

| Category | Use for | Consequence |
| --- | --- | --- |
| `UTILITY` | Something the recipient already signed up for: session materials, feedback on training they attended, appointment reminders | Not subject to marketing limits. Delivers reliably. |
| `MARKETING` | Promotion, invitation to something new, anything with a persuasive frame | Per-user marketing limits, easier opt-out, worse delivery |
| `AUTHENTICATION` | One-time passcodes only | Strict format rules |

**Research surveys are almost always UTILITY** - the respondent enrolled in the
study or attended the training. But Meta assigns the final category by reading
the copy, and promotional framing pulls it toward MARKETING.

Real example from this repo. The 2023 template was approved as MARKETING:

> Glad to have you as part of the 2023 India Research Staff Training! I'm here
> to give you a **sneak peek into something special** coming up later this
> week... **Discover more about the vibrant destination of Goa.**

The 2026 rewrite reads as UTILITY:

> Hi {{1}}, you are registered for the 2026 India Research Staff Training. We
> will use WhatsApp to share session materials and to collect your feedback
> during the training.

Same information, no persuasion. Plain statements of fact about something the
person already agreed to. That is the lever.

## Step 3 - languages

Each language is a **separate template with its own approval**. There is no
multi-language template.

Ask explicitly, and be precise about the locale code:

- `en` vs `en_US` vs `en_GB` are distinct templates. This account already has
  RST templates split across `en_US` and `en_GB` for no apparent reason, which
  is now permanent clutter.
- **Pick one and use it consistently.** Prefer the bare code (`en`, `es`)
  unless there is a real regional difference in the copy.
- For India, English is usually right, but ask - Hindi or a regional language
  may be expected, and that doubles the review cycles.

Check what already exists before creating anything:

```bash
just template-list --filter rst
```

An approved template with year-agnostic wording can often be reused rather than
recreated. Approvals do not expire; they only lapse if Meta pauses the template
for negative user feedback (3h, then 6h, then permanent deactivation).

## Step 4 - choose the content type

| Type | Business-initiated? | Notes |
| --- | --- | --- |
| `twilio/text` | Yes, with approval | Simplest, most likely to be approved |
| `twilio/quick-reply` | Yes, with approval | Up to 10 buttons; only 3 if used unapproved in-session. Button titles max 20 chars |
| `twilio/media` | Yes, with approval | Media type is frozen at approval - a template approved with an image can never send a video |
| `twilio/card` | Yes, with approval | All buttons must be the same action type |
| `twilio/list-picker` | **No** | Cannot start a conversation |
| `twilio/location` | **No** | Cannot start a conversation |

For a survey opt-in, `twilio/quick-reply` with a single Start button is usually
best: it gives the respondent a one-tap way to open the 24-hour window, and the
button payload is readable in Studio via
`widgets.<name>.inbound.ButtonPayload` for a Split widget.

**Keep button IDs stable across years.** If the 2023 flow branched on
`Start_payload`, reuse that exact ID and the existing Split widgets keep
working without rewiring.

## Step 5 - draft into a file, never straight to the API

Template definitions live in `templates/<name>.json` so the exact wording sent
to respondents is version-controlled and reviewable in a diff. Given
immutability, the diff is the only safety net.

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

Naming: `<project><year>_<purpose>`, lowercase with underscores. Meta requires
lowercase and underscores for the name it stores, and the year makes it obvious
later which round a template belongs to.

## Step 6 - what gets templates rejected

`check_variables()` in `src/requests_to_twilio/templates.py` warns about the
first three automatically. Check the rest by reading.

- **A variable with no sample.** Every `{{1}}` needs an entry in `variables`.
- **Body starts or ends with a variable.** `"Hi {{1}}"` as the entire opening
  is high-risk; put words around it.
- **Button title over 20 characters.** WhatsApp truncates silently.
- **Two adjacent variables** - `{{1}} {{2}}` reads as spam.
- **Promotional framing in a UTILITY submission** - Meta re-categorises rather
  than rejecting, which is worse because it is silent.
- **URLs with no context.** Templates support links, but a bare link looks
  like phishing. WhatsApp does not render URL previews in templates.
- **Claims the flow cannot keep.** If the copy says "confidential", the
  pipeline must encrypt.

Never put PII in a template body. Templates are account-wide, permanent, and
visible to anyone with Console access. Names go in `variables` at send time.

## Step 7 - create, review, submit

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

**Never run `template-create --submit` on a first draft.** The combined flag
exists for templates whose wording has already been reviewed.

**Never submit on the user's behalf without explicit confirmation in that
turn.** Both commands prompt, but do not answer the prompt for them - the
decision is theirs and it is permanent.

If rejected, `just template-status` shows Meta's reason. The template cannot be
fixed; write a new definition under a new name, and record what was rejected in
the `_comment` of the replacement so the same mistake is not repeated.

## Step 8 - wire it into the flow

Once approved, the template is used by its `HX` SID:

1. In Studio, open the widget that sends the first message.
2. Set **Message Type** to *Content template*.
3. Paste the `HX` SID; fill in variables.
4. Under Advanced Configuration set **Send message from** to the WhatsApp
   sender.

Then put the SID in `.env` as needed and re-pull the flow so the repo reflects
what is deployed:

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
are writing new code against the Content API directly, that is the trap.

Categories are `UTILITY`, `MARKETING`, `AUTHENTICATION` - the `CATEGORIES`
constant.

## Checklist before submitting

- [ ] Is this template genuinely needed, or does the 24-hour window cover it?
- [ ] Does an approved template already exist that could be reused?
- [ ] Category chosen deliberately, and the copy actually reads that way?
- [ ] Language code consistent with the rest of the account?
- [ ] Every variable has a sample; body does not start or end with one
- [ ] Button titles under 20 characters; IDs match any existing flow branching
- [ ] No PII in the body
- [ ] Any confidentiality claim is backed by encryption in the flow
- [ ] The definition is committed, so the wording is reviewable later
- [ ] The user has explicitly confirmed submission in this turn
