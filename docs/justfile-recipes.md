# The recipes

A recipe is a short command you type in a terminal, such as `just survey check
my_survey.json`. Typing `just` on its own lists the eight groups of recipes,
called modules, in the order you need them. Typing `just survey` (or any other
module name) lists that module's recipes.

| Module | For | Page |
|---|---|---|
| `just setup` | getting a laptop and a Twilio account ready, once | [setup](recipes/setup.md) |
| `just approved` | the opening and closing messages Meta has to approve | [approved](recipes/approved.md) |
| `just survey` | writing a survey and turning it into a flow | [survey](recipes/survey.md) |
| `just flow` | putting the flow on Twilio | [flow](recipes/flow.md) |
| `just round` | sending the survey to people | [round](recipes/round.md) |
| `just data` | the answers that came back | [data](recipes/data.md) |
| `just demo` | a ready-made example survey | [demo](recipes/demo.md) |
| `just dev` | tests and formatting, for people changing this code | [dev](recipes/dev.md) |

## Words you will meet

- **Twilio** is the service that sends and receives the WhatsApp messages. Your
  team has an account with it, and `.env` holds that account's keys.
- **The sender** is the WhatsApp number your messages come from.
- **A flow** is the survey as Twilio runs it: every message, every wait for a
  reply, every check on an answer. It lives in Twilio Studio, and you never
  have to edit it by hand; `just survey build` writes it.
- **Meta** owns WhatsApp. It reviews any message sent to someone who has not
  written to you first. Those are the **approved messages**.
- **Twilio content** is the buttons and lists your questions show. It needs no
  approval, because it is sent after the person has replied.
- **The Functions** are two small programs on your Twilio account. One encrypts
  phone numbers and private answers; the other saves each person's answers.
- **The sample** is the spreadsheet of people to send to, one row each.

## Step by step: from a new laptop to your data

Steps marked "once" are done once per laptop or account. [START-HERE](../START-HERE.md)
explains every step in more detail, including the waits on Meta; this is the
checklist.

### 1. Set up (once)

```powershell
just setup install          # the tools this project needs, and the project
```

Copy `.env.example` to `.env` and fill in your Twilio account SID, auth token and
sender number. Then:

```powershell
just setup key              # makes the encryption keys
just setup functions        # puts the two Functions on your Twilio account
just setup mcp              # optional: lets Claude Code read your Twilio account
```

### 2. Get the opening and closing messages approved (once per wording)

Write each message in `templates/<name>.json` ([how](writing-templates.md)).
Then create it on Twilio, send it to Meta, and check back:

```powershell
just approved create templates/my_intro_en.json
just approved submit my_intro_en --category UTILITY     # cannot be undone
just approved status my_intro_en                        # repeat until "approved"
```

Approval takes minutes, sometimes a day. If messages you can use are already
approved, skip this step: the builder lists them for you.

### 3. Write the survey

```powershell
just survey builder
```

The builder opens in your browser. Start a new survey or open one, pick the
approved opening and closing messages, choose which sample column fills each
`{{1}}` (usually the person's name), write your questions, and save the file as
`my_survey.json`. If you prefer Excel, run `just survey new --output
my_survey.xlsx`, fill it in, and convert it with `just survey json
my_survey.xlsx`.

Then check it:

```powershell
just survey check my_survey.json      # fix anything it reports
```

### 4. Build the flow

```powershell
just survey content my_survey.json                     # creates the buttons and lists
just survey build my_survey.json --lang en             # writes flows/<name>.json
```

To save the answers in MotherDuck rather than a Google Sheet, add
`--publish-target motherduck` to the build.

### 5. Get the place the answers go ready

```powershell
just flow table flows/my_survey.json      # MotherDuck: creates the table
just flow header flows/my_survey.json     # Google Sheet: paste this into row 1
```

### 6. Put the flow on Twilio

```powershell
just flow deploy flows/my_survey.json --publish
```

`--publish` makes it the version Twilio runs; `just round send` refuses a flow
that has only a draft. The deploy prints the flow's ID, which starts with `FW`.
Then:

- In `.env`, set `TWILIO_FLOW_ID=FW…`, so `just round send` starts that flow.
- In the Twilio console, open **Messaging → Senders → WhatsApp senders → your
  sender**, and under *when a message comes in* choose **Studio Flow** and pick
  the flow (or paste
  `https://webhooks.twilio.com/v1/Accounts/<AccountSid>/Flows/<FlowSid>`), so
  that replies reach it.

Do this a few minutes before you send: for a short while after the change, some
replies can still go to the flow the sender used before.

### 7. Send the survey

```powershell
just round intake scripts/build_my_sample.py            # turns your list into a sample
just round send my_sample caseid,name --dry-run         # runs every check, sends nothing
just round send my_sample caseid,name                   # sends, then watches for an hour
```

Send it to yourself first, with a one-row sample holding your own number.

### 8. Get the answers back

```powershell
just data decrypt responses.csv
just data check responses_decrypted.csv
```

If someone's answers seem to be missing, `just data fetch --since 2026-08-01`
shows what Twilio recorded.

### After a rehearsal

Before the real round, clear the rehearsal's files with `just round reset
my_sample.xlsx --round --yes`. Otherwise the test numbers and rows carry over.
