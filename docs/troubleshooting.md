# Troubleshooting

Ordered by how expensive the mistake is, not by how often it happens. The ones at
the top are the reason this repository has a checks layer at all: **they all
report success at the moment they fail.**

## Silent failures — nothing errors, the data is wrong

### Everyone got the survey, nobody's answers came back

The WhatsApp sender's inbound webhook points at a different flow. Messages send,
respondents reply, the replies reach something else, and your executions sit
untouched until they time out.

A **WhatsApp sender is a different Twilio resource that shares digits with a
phone number** and has its own webhook. Repointing the number's `sms_url` does
nothing. Fix under **Messaging → Senders → WhatsApp senders → your sender**.

`rtt launch` checks this before sending. Do not pass `--skip-preload-check`,
which disables it too.

### Every message said "Hi ," and a column is blank

A preloaded column name does not match what the flow references. Twilio resolves
an unknown `{{flow.data.x}}` to an empty string rather than erroring.

`rtt launch` reports this before sending, including a case-mismatch hint —
`Name` and `name` are different.

### Respondents are being read wording you already changed

You reworded a question, rebuilt, redeployed, and the old text still goes out.

Twilio has **no update operation for content**. `rtt template create` refuses to
overwrite a template that already exists, and `just demo-templates-create` passes
`--skip-existing`, so a rebuild regenerates the definition file on disk and
leaves the template on the account exactly as it was.

Nothing downstream can see it. The flow references the template **by SID**, and
the SID is still valid — so the flow JSON is correct, `just flow-check` passes,
the linter is clean and the tests are green, while the respondent reads the
previous wording.

This is not hypothetical. Adding a sixth question to the demo renumbered every
body to "of 6". Four templates already existed and kept saying "of 5". A live
respondent was asked six questions numbered out of five, and the only thing that
caught it was reading her transcript afterwards.

`just build-demo-flow` now **blocks** on this: it compares each stored template
against the definition it just wrote and refuses to build, naming what drifted.
To fix:

```powershell
just demo-templates-sync
```

That deletes and recreates only what actually differs. It **refuses on anything
submitted to Meta** — approval attaches to the SID, so replacing an approved
template throws the approval away and the next round fails at send time with
`63016`. New wording on an approved template means a new template under a new
name, and pointing the flow at that.

### The survey completed but there is no row

Two causes, both now caught by `just flow-check`:

- The publish widget's `fail` transition led to the closing message, so the
  respondent was thanked and the row never existed.
- The Functions were deployed **`private`**. A private Function is not callable
  over HTTP at all — only from another Function in the same service — so Studio
  gets `403 Unauthorized`, which reads like a credentials problem and is not.
  `protected` is what Studio needs. `just deploy-functions` sets it.

### A question is missing from the warehouse

The publish Function inserts only into columns that already exist and returns
**HTTP 200** either way. Re-run `just flow-schema` after any instrument change
and apply the difference.

### Every tap fell through to the retry nudge

A tapped **list row returns its `id`**, not its label. A tapped **quick-reply
button returns its title**. The two interactive types disagree and the docs do
not say so. `just flow-check` evaluates every option against the split and
reports any that can never match.

### A number typed on a numeric scale coded as the wrong option

On a scale reading `0 projects / 1 project / 2-3 projects`, a respondent who
means *one project* types `1` — which as a position is the *first* option,
`0 projects`. The builder now refuses bare digits on scales whose labels are
themselves numbers, so the respondent is asked again instead.

### The randomised arms collapsed into one

The sample file had no `arm` column, or it was misspelled, so `split_arm` sent
everyone down the default branch. The flow now routes that to `mark_arm_missing`
and publishes `set_arm_missing`, so it shows up as a value in the data instead of
as a suspiciously balanced result.

### A respondent said "thanks" and got the whole survey again

Politeness restarts flows: an inbound message from someone who just finished
creates a fresh execution. The demo flow answers cold inbound once and ends.
`flow-check`'s `respondent-initiated-start` reports flows that do not.

## Error codes

| Code | Meaning | Fix |
| --- | --- | --- |
| `63016` | free-form message outside the 24-hour window | the opener must be an approved template; the window opens only when they reply |
| `63027` | template not approved **for this WhatsApp Business Account** | it was approved under a different WABA. Being listed by the Content API says nothing about whether your sender can send it. Recreate it under yours |
| `21656` | a template variable resolved to empty | Meta rejects the whole send. Give variables a Liquid default: `{{flow.data.name \| default: 'there'}}` |
| `20001` | malformed API request | often a `limit` above the endpoint's page-size cap |
| `403` from a Function | the Function is deployed `private` | redeploy as `protected` |
| `Error downloading invalid media URL` | Meta could not fetch template media | Meta fetches from outside, so anything gated or on a private domain is unreachable |

## Setup problems

### `just mcp-list` says the Twilio server is not ready

It needs a **Standard API key**, which is a different credential from the auth
token `rtt` uses. Console → Account → API keys & tokens → Create API key. The
secret is shown once. Put it in `.env`, run `just mcp-setup`, then **restart
Claude Code**.

### `.env` looks right but the wrong credentials are used

An exported shell variable beats `.env`, by design — that is how you point a
command at a second account without editing anything. Check with
`echo $env:TWILIO_ACCOUNT_SID`.

A *blank* exported variable used to win too, which meant a stray empty
`TWILIO_ACCOUNT_SID` made every credential read as unset while `.env` was
perfectly fine. That one is fixed: an empty value is treated as absent.

### `just build-demo-flow` says the templates do not exist

It resolves content templates by name on your account. Run
`just demo-templates-create` first. It will name what is missing.

### `just build-demo-flow` says there is no `rtt-survey` service

Run `just deploy-functions`. The builder looks the Functions service up by name
rather than using pasted SIDs, which is what lets the same repository build a
working flow on any account.

### `uv sync --locked` fails in CI after a dependency PR

Dependabot updates `pyproject.toml`; the lockfile has to move with it. Run
`uv lock` and commit the result.

### Unicode errors on Windows when redirecting output

Fixed — `rtt` reconfigures stdout to UTF-8. If you see it from a plain `python`
invocation, set `PYTHONUTF8=1`.

## When a check disagrees with you

`flow-check` errors block a deploy; `data-check` findings are warnings. If a
finding is wrong, that is worth reporting rather than working around: two of
these checks were wrong on their first attempt and reported green on a genuinely
broken round. A check that answers the wrong question is worse than no check,
because it is trusted.
