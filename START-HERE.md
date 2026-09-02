# Start here

You want to run a survey over WhatsApp: send a question to a list of people,
collect what they reply, and get it back without their names and numbers ending
up somewhere they should not be.

This page is the whole path, in order. It assumes no Twilio experience.

**Read the timing note first.** Steps 1 to 3 involve Meta and can take **days to
weeks**. Everything after them takes an afternoon. Teams get caught out by
starting at step 5, finding it works in a sandbox, and then discovering that
their real sender is three weeks from approval. If your round has a date, start
step 1 today and do the rest while you wait.

| | Step | Waiting on | Roughly |
| --- | --- | --- | --- |
| 1 | A Twilio account | nobody | minutes |
| 2 | A WhatsApp sender | **Meta business verification** | **days to weeks** |
| 3 | An approved opening template | **Meta template review** | **minutes to days** |
| 4 | Point the sender's replies at your flow | nobody | minutes |
| 5 | Install this toolkit | nobody | minutes |
| 6 | Generate an encryption keypair | nobody | seconds |
| 7 | Deploy the two Twilio Functions | nobody | minutes |
| 8 | Create the warehouse table | nobody | minutes |
| 9 | Build and deploy the flow | nobody | minutes |
| 10 | Launch to a test number | nobody | minutes |
| 11 | Get the data back | nobody | minutes |

---

## 1. A Twilio account

Sign up at [twilio.com](https://www.twilio.com). From **Console → Account Info**
you will need two values later:

- **Account SID** — starts `AC`
- **Auth Token** — treat it like a password; it can send messages from your
  number to anyone

## 2. A WhatsApp sender

This is the long pole. WhatsApp will not let a business message people from
nowhere, so Meta verifies you first.

In the Twilio Console: **Messaging → Senders → WhatsApp senders → New sender**.
You will be asked to connect a Meta Business account and verify the business.
Budget weeks, not days, the first time an organisation does this.

For *learning* the tooling you can skip this and use Twilio's **WhatsApp
sandbox** (Messaging → Try it out). The sandbox works immediately, but only
messages people who have opted in by texting a join code, so it is for testing,
never for a real sample.

## 3. An approved opening template

WhatsApp only lets you write to someone unprompted using a **template Meta has
approved in advance**. Once they reply, a 24-hour window opens and you can send
anything.

Two messages in a survey are therefore business-initiated and need approval —
the **bookends**:

- the **opener**, obviously; and
- the **closing message to someone who never replied**, because their window
  never opened.

Everything in between — consent buttons, answer lists, retry nudges, the closing
message to someone who did reply — is inside the window and needs nothing.

```powershell
just template-create templates/data_use_demo_intro_en.json
just template-create templates/data_use_demo_close_en.json
just template-submit data_use_demo_intro_en --category UTILITY
just template-submit data_use_demo_close_en --category UTILITY
just template-status data_use_demo_intro_en
```

Three things worth knowing before you submit:

- **Submission is irreversible.** An approved template can never be edited, only
  deleted and recreated under a new name. `template-submit` asks for confirmation
  for this reason.
- **The category is not a hint.** Leading with an incentive lands in MARKETING;
  leading with the consent basis lands in UTILITY. Marketing templates are easier
  for a respondent to have blocked.
- **Approval is per WhatsApp Business Account, not per Twilio account.** A
  template approved under a colleague's WABA is visible in your Content API and
  still fails to send with error `63027`. If you see that code, the template was
  approved somewhere else and has to be recreated under yours.

Consent wording stays out of Meta review on purpose: it is sent inside the
window, so the full IRB text never goes through them and can change without a
resubmission.

## 4. Point replies at your flow

**The single most expensive mistake in this repo's history.** A WhatsApp sender
is a different Twilio resource that merely shares digits with a phone number, and
it carries **its own** inbound webhook. Repointing the phone number's `sms_url`
does nothing for WhatsApp.

Set it under **Messaging → Senders → WhatsApp senders → your sender → inbound
webhook**, pointing at the flow you are about to deploy.

You do not have to get this right from memory: `rtt launch` checks it before
sending and refuses to run a round whose replies would land somewhere else.
Skipping it means messages send fine, respondents reply, the replies reach a
different flow, and the round collects nothing while reporting success.

## 5. Install the toolkit

You need [uv](https://docs.astral.sh/uv/), [just](https://just.systems/) and
Node. On Windows:

```powershell
winget install Casey.Just astral-sh.uv
just get-started
```

Then create your `.env`:

```powershell
cp .env.example .env
```

Fill in `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_NUMBER` (with the
`whatsapp:` prefix — `whatsapp:+15551234567`). Leave the rest for now.

Nothing secret is ever passed as a command-line flag: arguments land in shell
history and are readable by other processes on the machine.

## 6. Generate an encryption keypair

```powershell
just keygen
```

This prints a **public key** and writes a **private key** to
`rtt_private_key.txt`.

- The **public key** goes to Twilio. It can only encrypt. If it leaks, nothing is
  exposed.
- The **private key** stays on your machine and is the only thing that can read
  responses back.

> **Lose the private key and the collected identifiers are gone.** There is no
> reset, exactly as with a SurveyCTO private key. Back it up somewhere
> access-controlled *before* you collect anything, and write down who else has it.

Point `.env` at it with `ENCRYPTION_PRIVATE_KEY_FILE=rtt_private_key.txt`.

## 7. Deploy the two Twilio Functions

```powershell
just deploy-functions
```

This creates a Functions service called `rtt-survey` and deploys two files from
`twilio_functions/`: one that encrypts identifiers, one that writes the row to
your warehouse. It sets their environment variables from your `.env`, so nothing
secret is pasted into the Console.

You do not need to copy anything from its output. Everything is looked up by name
when the flow is built.

## 8. Prepare the destination

Pick one. They are peers, chosen per build, and the choice is yours: Sheets
needs a spreadsheet and a service account, MotherDuck needs a warehouse account.
**Sheets is the default** because it is the lower barrier, not because it is the
lesser option — a warehouse account should not be the first step of somebody's
first WhatsApp survey.

Whichever you pick, **the publish step never creates the destination.** Guessing
a schema for survey data produces wrong types, and a silent create would hide a
typo. Both Functions write only into columns that already exist, so a question
with nowhere to go is dropped **silently behind an HTTP 200**. Re-run the command
below whenever you add a question.

### Google Sheets (default)

Print the header row the flow expects, and paste it into row 1 of a tab:

```powershell
just flow-header flows/data_use_demo_en.json
```

Create the sheet, share it with your service account's address as an Editor,
then set `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEET_ID` and
`GOOGLE_SHEET_TAB` in `.env`.

**Set `GOOGLE_SHEET_TAB` explicitly.** Unset means *the first visible tab*, so
adding a tracking tab later silently redirects every submission into it.

### MotherDuck

Print the DDL from the flow itself and run it:

```powershell
just flow-schema flows/data_use_demo_en.json --table my_db.main.my_round
```

Then set `MOTHERDUCK_TOKEN`, `MOTHERDUCK_DATABASE`, `MOTHERDUCK_HOST` and
`MOTHERDUCK_TABLE` in `.env`, and build with
`just build-demo-flow "--lang en --publish-target motherduck"` in the next step —
**the default is Sheets, so a MotherDuck round has to say so.** Configuring
MotherDuck and building without that flag gives you a flow that publishes to a
sheet you never made, and `deploy-functions` will not catch it.

## 9. Build and deploy the flow

```powershell
just demo-templates-create        # the in-session templates the questions need
just build-demo-flow --lang en    # writes flows/data_use_demo_en.json
just flow-deploy flows/data_use_demo_en.json --publish
```

`build-demo-flow` resolves your Functions service and your content templates by
name, so the flow it writes points at *your* account.

`flow-deploy` refuses to ship a flow that fails the checks. That is deliberate:
refusing to deploy costs you a fix, whereas a defective flow costs you a round.

### Writing your own survey, rather than the demo

The demo is a specimen. For your own instrument you do not edit Studio and you do
not copy someone else's flow — you describe the survey as spreadsheet rows:

```powershell
just survey-template --output my_survey.xlsx   # a documented starter workbook
# ...fill it in in Excel...
just survey-json my_survey.xlsx                # → my_survey.json, the tracked copy
just survey-check my_survey.json               # must pass before you build
```

One row is one question *and* the whole subgraph it becomes — eight Studio widgets
for a closed question with retries, three for open text. `sample_template.xlsx` in
the repository root is a filled-in example you can open right now, and its
`help-survey` sheet documents every column.

**This format is for surveys.** It assumes consent is asked before any question,
that every path publishes one row per respondent, and that one execution is one
person answering once. Reminder campaigns, multi-wave interventions and
notification flows break those assumptions — build those as Studio flows and check
them with `just flow-check`, which judges a graph on its own terms.

Full guide: [docs/writing-a-survey.md](docs/writing-a-survey.md).

## 10. Launch to a test number

Put your own number in a sample file first. `sample_input.xlsx` is the shape:

| Number | caseid | name | arm |
| --- | --- | --- | --- |
| `whatsapp:+15555550100` | RST2026-001 | Priya | 1 |

`Number` is required. Everything else is preloaded data the flow can read as
`{{flow.data.<column>}}` — the equivalent of a SurveyCTO preload. Column names
must match the flow exactly and must be `[A-Za-z0-9_]` only.

```powershell
# See exactly what would be sent, and run every pre-flight check
just launch "sample_input.xlsx --columns caseid,name,arm --dry-run"

# Send for real
just launch "sample_input.xlsx --columns caseid,name,arm"
```

Answer it on your phone. Then confirm the row arrived before you launch at
anyone else.

A delivery tracker is written next to your input as `sample_input_output.csv`,
flushed after every send, so an interrupted run does not lose the record of who
was already contacted. Re-running without `--resume` is refused rather than
double-sending.

## 11. Get the data back

First get the table out of MotherDuck. In the MotherDuck UI, run

```sql
SELECT * FROM your_database.main.your_round;
```

and use its download button to save a CSV. (There is deliberately no
`rtt pull-from-warehouse`: exporting is one click, and a command that silently
wrote respondent data to disk is not something worth adding.)

Then, locally:

```powershell
# Decrypt with your private key. Writes responses_decrypted.csv
just decrypt "responses.csv"

# During a round: does the data look right?
just data-check "responses_decrypted.csv"

# What does Twilio think happened? Useful when a row seems missing
just fetch "--since 2026-08-01"
```

Encrypted columns are detected automatically — they carry a `v2:` marker — so
there is no list of column names to keep in sync.

The decrypted file is plain-text PII the moment it is written. Store it per your
organisation's policy for that classification.

---

## What to read next

| | |
| --- | --- |
| [docs/setup.md](docs/setup.md) | Every configuration value, and where it comes from |
| [docs/justfile-recipes.md](docs/justfile-recipes.md) | Every `just` recipe, grouped by when you reach for it |
| [docs/running-a-round.md](docs/running-a-round.md) | Every command of a round in order, and what each check blocks on |
| [docs/publishing.md](docs/publishing.md) | Where the data and the delivery tracking go, and how to choose |
| [docs/flow-design.md](docs/flow-design.md) | Writing your own instrument, not the demo |
| [docs/writing-templates.md](docs/writing-templates.md) | Template copy, categories, and Meta review |
| [docs/encryption.md](docs/encryption.md) | What the encryption protects, and what it does not — read before an IRB submission |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Error codes, and the failures that look like success |
| [docs/portability.md](docs/portability.md) | What is Twilio-specific if you ever move providers |

## The one thing to remember

Every serious defect this project has shipped **reported success at the moment it
failed**: a survey completed and no row written, a message sent to a flow that
was not listening, every tap falling through to a retry nudge, a warehouse
returning 200 while dropping columns.

So the checks are not ceremony. `just flow-check` before a round and
`just data-check` during one exist because none of that was visible in the Studio
editor, and all of it was visible in the data months too late.
