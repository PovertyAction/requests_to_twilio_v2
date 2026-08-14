# If you ever move off Twilio

This toolkit is Twilio-specific and there is no provider abstraction. That is a
deliberate choice: a second provider would be guesswork until someone actually
needs one, and the wrong abstraction costs more than none. But the seams are
real, and this page names them so a future port is a scoping exercise rather
than an archaeology one.

The context: Twilio is one of many WhatsApp Business Solution Providers. Meta's
Cloud API is free and direct; Infobip, 360dialog, Gupshup and others resell the
same underlying thing with different tooling. What differs between them is
message sending, template management, and whether they offer anything resembling
Studio. What does not differ is everything a *survey* needs on top.

## What is Twilio-specific

| Module | Coupled to | Notes |
| --- | --- | --- |
| `launcher.py` | Studio Executions API | The idea — read a sample, send one message per row, track delivery, resume — is provider-independent. The API call is one function, `_create_execution`. |
| `flows.py` | Studio flow JSON | The whole widget-graph model. No other provider has Studio, so this is a rewrite, not a port. |
| `templates.py` | Twilio Content API | Meta's template API is the same concepts with different names. Category, variables, approval status and the irreversibility of submission all survive. |
| `fetch.py` | Studio Executions API | Reconciliation itself is general; where the record lives is not. |
| `deploy_twilio_functions.py` | Twilio Serverless | Whatever runs the encryption and publish code has to be redeployed. |
| `twilio_functions/*.js` | the Twilio Function handler signature | The bodies are ordinary Node; only `exports.handler` and `context` are Twilio's. |

## What is provider-agnostic and moves unchanged

| Module | |
| --- | --- |
| `crypto.py`, `decryptor.py` | X25519 + AES-GCM has nothing to do with transport. The only requirement is that *something* runs the encryption before the row is stored. |
| `warehouse.py` | MotherDuck loading. Independent of how the data arrived. |
| `hfc.py` | Data-side checks operate on a collected frame. |
| `config.py`, `log.py` | Including the phone-number masking filter, which any provider needs. |
| The sample-file and preload conventions | "One row per respondent, a `Number` column, everything else preloaded" is how survey samples work, not how Twilio works. |

## The ideas worth carrying over even in a rewrite

These are the expensive lessons, and none of them are Twilio facts:

- **A break-off must still produce a row.** A respondent who stopped replying is
  data. If a timeout path writes nothing, attrition becomes indistinguishable
  from never having contacted them.
- **Publish per submission, not at the end.** It makes the destination a live
  delivery dashboard, and it means a crash loses one row rather than all of them.
- **Encrypt identifiers before they reach shared storage**, with a public key, so
  platform access does not imply access to respondents.
- **Check for failures that report success.** Every serious defect this project
  has shipped looked healthy at the moment it failed. That is a property of
  messaging pipelines, not of Twilio.
- **A consent gate, an opt-out path, and a final-status variable** are survey
  requirements. No provider will give you them.
- **Only business-initiated messages need pre-approval** — the opener, and
  anything sent to someone whose 24-hour window never opened. That window is a
  WhatsApp rule, so it applies through every BSP. Anything a BSP charges for
  approving *in-session* content is their own invention.

## Roughly what a port costs

The message-sending layer is small — `launcher.py` is one API call surrounded by
batching, retries and a tracker. The template layer is a like-for-like mapping.

The expensive part is `flows.py` and the flow builder, because Studio's
visual-graph model is Twilio's alone. On Meta's Cloud API you would own the
conversation state machine yourself: a webhook receiver, a per-respondent state
store, and your own timeout scheduling. That is a real service to run, not a
module to port — but it is also the part where the *checks* transfer as design
requirements even though none of the code does.
