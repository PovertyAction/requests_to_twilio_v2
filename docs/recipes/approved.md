# just approved

Meta, which owns WhatsApp, has to approve any message sent to someone who has not
written to you. In a survey that means two messages: the opener, and the closing
for people who never replied. Once a person replies, everything else, buttons and
lists included, is sent without approval; that part is made by `just survey
content`.

```powershell
just approved create templates/my_intro_en.json
just approved submit my_intro_en --category UTILITY
just approved status my_intro_en
```

| Recipe | What it does |
|---|---|
| `list` | Lists the messages on your account, and whether Meta has approved each. |
| `create FILE` | Creates the message on Twilio from its file in `templates/`. It does not go to Meta yet, so you can still change the wording. |
| `submit NAME` | Sends it to Meta for approval. This cannot be undone: once submitted, the wording can never change. Read it once more first. |
| `status NAME` | Shows whether Meta has approved it. This usually takes minutes, sometimes a day. |
| `delete NAME` | Deletes a message you have not submitted, so you can rewrite it. Twilio cannot edit a message, so this is how a draft gets fixed. |

How to write a message Meta approves: [writing templates](../writing-templates.md).
