# Writing WhatsApp templates

A template is a message Meta has approved in advance. You need one to write to
somebody who has not written to you — which for a survey means the two
**bookends**: the opener, and anything sent to a person who never replied and
therefore never opened a 24-hour window. Everything between those two is
in-session and needs no approval at all.

**A submitted template can never be edited.** Not the wording, not the category.
Revising one means deleting it and creating a new one under a different name, and
waiting for review again. So the design conversation happens before submission,
not after.

## What needs approval, and what does not

| | Needs Meta approval |
| --- | --- |
| The opening message | **yes** — it is business-initiated |
| A closing message to someone who never replied | **yes** — their window never opened |
| Consent buttons | no |
| Answer lists | no — and a list picker **cannot** be approved even if you try |
| Retry nudges, closing messages to people who replied | no |

This is why consent stays in-session on purpose: the full IRB wording never goes
in front of Meta, and can change without a resubmission.

## Category is set by how the ask is framed

Category is requested at submission and Meta may disagree with you. From the
approved corpus on our own account, the pattern is consistent:

- Every opening that **leads with an incentive** landed in **MARKETING** — even
  one that also cited the prior survey.
- Every opening that **leads with the consent basis** ("you agreed to be
  contacted again about…") landed in **UTILITY**.

Prefer UTILITY where the framing honestly supports it. Marketing messages are
easier for a respondent to have blocked at the account level, and the framing is
more accurate for research contact anyway.

Two near-identical thank-you messages sit in different categories on our account,
so the category you request is not merely a hint.

## Writing the copy

- **Say who you are in the first line.** A message from an unknown number asking
  questions is indistinguishable from a scam, and that is the respondent's
  reasonable prior.
- **Say why you have their number.** "You took part in X and agreed we could
  contact you again" does more for response rate than any incentive.
- **Give every variable a default.** An empty variable fails the send with error
  `21656` — the *whole message*, not just the placeholder. Use
  `{{flow.data.name | default: 'there'}}`. A blank name should cost you a
  slightly impersonal greeting, not a respondent.
- **Keep the opener short.** Across 392 question bodies in IPA's own WhatsApp
  instruments (2022-2026) the median is 227 characters and 7 lines, and the
  longest in four years is 895 — against an API limit of 1024. The limit was
  never the binding constraint; attention is.
- **Media must be publicly fetchable.** Meta downloads it from outside, so an
  image behind any kind of gate fails review with `Error downloading invalid
  media URL`. A rejection blamed on content is often really this.

## Approval is per WhatsApp Business Account

This one stops rounds. A template approved under a colleague's WABA is visible in
*your* Content API listing, reports `approved`, and still fails to send with
error `63027` from every sender you own.

**Being listed by the API says nothing about whether your sender can send it.**
If you inherit a flow from another team, assume its templates need recreating
under your WABA. Test one send before planning around them.

## The commands

```powershell
just template-list --filter rst
just template-create templates/my_intro.json
just template-status my_intro
just template-submit my_intro --category UTILITY   # IRREVERSIBLE
just template-delete my_draft --yes
```

`create` makes the template in Twilio without submitting it, so the wording can
still change. `delete` exists because Twilio has no update operation for content;
it refuses anything already submitted to Meta.

`submit` asks for confirmation and refuses a list picker outright rather than
letting Meta reject it.

## A template definition

```json
{
  "friendly_name": "my_intro",
  "language": "en",
  "variables": { "1": "there" },
  "types": {
    "twilio/text": {
      "body": "Hello {{1}}, this is IPA. You took part in our survey in June and agreed we could contact you again..."
    }
  }
}
```

Keep one file per template in `templates/`. The generated in-session ones live in
`templates/generated/` and are rebuilt by `just build-demo-flow`, so edit the
builder rather than those files.

## Before you submit

Read it aloud as though you received it from a number you do not recognise. Then
check the variable defaults, then the category. Then submit, because you cannot
change any of it afterwards.

---

The agent-facing version, with the full corpus analysis and more examples, is in
`.claude/skills/whatsapp-template/`.
