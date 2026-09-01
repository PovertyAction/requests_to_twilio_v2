# Every recipe in the Justfile

`just` is the entry point to this repo. `just --list` prints the recipes; this
page says what each one is for and when you would reach for it.

Recipes are grouped by the order you meet them, not alphabetically. If you are
new here, read [START-HERE.md](../START-HERE.md) first — it walks a round end to
end, and this page is the reference you come back to.

A note on what is missing, because it surprises people: **there is no recipe that
turns a survey spec into a Studio flow.** `rtt survey` reads, checks and converts
an instrument between JSON and Excel, but the only thing that emits a flow
definition is `just build-demo-flow`, which reads its own Python tables in
`scripts/build_data_use_demo.py`. Changing the demo's questions means editing
that script — it is the only description of that instrument.

There used to be a second one. `just export-demo-spec` lifted those tables into
spec form and its output was committed as `surveys/data_use_demo.json`. Both have
been removed: the exported copy went stale three hours after it was generated,
could not be regenerated once the instrument gained a fifth question, and read
like the authoritative description of a survey it no longer described.

## Setting up

Run these once, when you clone the repo or when the toolchain changes.

| Recipe | What it does |
|---|---|
| `just` | Lists every recipe. The default when you type `just` with no arguments. |
| `just pre-install` | Installs the toolchain — uv, gh, Node, markdownlint. Platform-specific; Node is not optional, because the interop test runs the real Twilio JavaScript and the MCP server launches with npx. |
| `just get-started` | `pre-install` then `venv`. The one command for a fresh machine. |
| `just venv` | Syncs dependencies with uv and installs the pre-commit hooks. |
| `just clean` | Deletes `.venv`. |
| `just update-reqs` | Upgrades the lockfile, re-syncs, and updates pre-commit hooks. |
| `just system-info` | Prints CPU, OS family and OS. Useful when a bug looks platform-specific. |
| `just mcp-setup` | Writes the Twilio MCP credentials from `.env` into `.claude/settings.local.json`, which is gitignored. `.mcp.json` is committed and names credentials as `${VAR}` rather than holding them. Restart Claude Code afterwards and run `/mcp` to confirm. |
| `just mcp-list` | Shows which MCP servers the project defines and whether each is ready. |

## Writing an instrument

A Studio flow is 84 widgets for 5 questions and nobody can review that. The spec
is the same instrument as ~20 rows, in the shape SurveyCTO users already know.

**The JSON is canonical** — it is what git carries and what a reviewer diffs. The
workbook is a generated view, gitignored, and it is what you edit.

| Recipe | What it does |
|---|---|
| `just survey-template` | Writes a documented starter workbook to fill in — a small working instrument with one row of every type, not an empty sheet. Refuses to overwrite. |
| `just survey-xlsx FILE` | Regenerates the editable workbook from canonical JSON. Overwrites: the workbook is a view, not a document with a history. |
| `just survey-json FILE` | Reads an RA's workbook edits back into the tracked JSON. **The load-bearing direction** — whatever does not survive this trip is silently lost. |
| `just survey-rows FILE` | Prints the whole instrument in the terminal, without opening Excel. |
| `just survey-check FILE` | The instrument-side equivalent of XLSForm validation. It does not read the option patterns and constraints, it *runs* them, and reports where each possible reply lands. Exits non-zero, so it can gate a build. |
| `just survey-sample` | Regenerates the committed `sample_template.xlsx`. Only needed when the schema or starter content changes — a test fails when it has drifted. |

The editing loop:

```sh
just survey-template                  # start from the documented example
just survey-xlsx my_survey.json       # make the workbook
# ...edit it in Excel...
just survey-json my_survey.xlsx       # bring the edits back
just survey-check my_survey.json      # then commit the JSON
```

## WhatsApp templates

Every message sent **before** the respondent replies must come from a
Meta-approved template. Once they reply, a 24-hour window opens and the rest of
the questionnaire runs free-form with no approval at all. In practice that means
only the two bookends — the opener and the closing message to people who never
answered — ever go to Meta.

| Recipe | What it does |
|---|---|
| `just template-list` | Lists templates and their Meta approval status. |
| `just template-create FILE` | Creates the template in Twilio but does **not** submit it, so the wording can still change. |
| `just template-submit NAME` | **Irreversible.** Submits to Meta. A submitted template can never be edited — only deleted and recreated under a new name. Review the wording first. |
| `just template-delete NAME` | Deletes an unsubmitted template so its wording can be redone. Twilio has no update operation for content, so this is how a draft gets revised. Refuses anything already submitted. |
| `just template-status NAME` | Shows one template's approval status. |
| `just demo-templates-create` | Creates the demo flow's in-session content templates from `templates/generated/`. None is ever submitted to Meta. |
| `just demo-templates-sync` | Makes Twilio match the repo: deletes and recreates any template whose wording has drifted. Refuses on anything submitted to Meta. **Run this after changing a question.** |

> **Content templates are immutable.** `demo-templates-create` uses
> `--skip-existing`, which leaves an existing template alone rather than making a
> duplicate. That is what you want when re-running a build — and exactly what you
> do **not** want after changing a question's wording or options, because the
> flow resolves templates *by friendly name* and then references them *by SID*.
>
> How that fails depends on what you changed, and the quieter case is the
> dangerous one. Change an **option** and the new split refuses every answer, so
> you find out immediately. Change only the **wording** and nothing objects at
> all: the flow is correct, `flow-check` passes, the linter is clean, the tests
> are green, and respondents are read the old text. That happened — six questions
> went out numbered "of 5" after a sixth was added, and only a respondent's
> transcript revealed it.
>
> `just build-demo-flow` now blocks on this, comparing each stored template
> against the definition it just wrote and naming what drifted. The fix is
> `just demo-templates-sync`, which replaces only what actually differs. Do not
> rely on remembering to `template-delete` by hand.

## Building and shipping a flow

| Recipe | What it does |
|---|---|
| `just build-demo-flow` | Builds the demo flow and the templates its questions need, both languages from one structure and two string tables. `--lang en` for one. `--publish-target gsheets` writes to a spreadsheet instead of MotherDuck. Needs live Twilio credentials: content templates are looked up by name. |
| `just flow-check [FILE]` | The instrument-side equivalent of high-frequency checks: verifies the survey was coded correctly. Omit the file to check every flow on the account. Exits non-zero on an error. |
| `just flow-schema FILE` | Prints `CREATE TABLE` DDL matching what the flow publishes. **Run this after changing the instrument** — the publish Function only inserts into columns that already exist, so a new question with no column is dropped silently behind a 200. |
| `just flow-header FILE` | The Google Sheets counterpart: prints the header row for row 1 of the target sheet. Same reason, same failure — `publish_gsheets` matches parameters to columns by header name. |
| `just flow-deploy FILE` | Deploys a definition. Refuses to ship one that fails the checks. |
| `just flow-list` | Lists the Studio flows on the account. |
| `just flow-pull NAME` | Saves a flow definition into `flows/` so it can be reviewed and diffed. |
| `just deploy-functions` | Deploys `encrypt_fields.js` and **both** publish Functions. A Twilio deployment is the complete set of functions in its build, so deploying a subset removes the others rather than leaving them alone. Each target's credentials are optional; only having none is an error. |

## Running a round

| Recipe | What it does |
|---|---|
| `just keygen` | Generates the X25519 keypair. The public key goes to Twilio; the private key stays on your machine and is gitignored. |
| `just signups BUILDER` | Turns the sign-up export into a launch sample. Resolves each number to E.164 against **the country the form collected** rather than a default region, drops anybody who did not tick consent, and refuses a landline — WhatsApp only reaches mobiles. Carries every `caseid` and `arm` it has already assigned forward unchanged, so re-running as more people sign up moves nobody. Nothing it prints contains a phone number. |
| `just send SAMPLE` | Sends the round, **then watches it land for an hour**, rewriting the `tracking` tab every two minutes. `--resume` is baked in: `rtt launch` refuses to run over an existing tracker, and every send after the first is incremental because people keep signing up while a session runs. On a first run there is no tracker, so it sends to everybody. A dry run starts no tracker — nothing was sent, so there is nothing to watch. |
| `just round-reset SAMPLE` | Clears the `data` and `tracking` tabs, optionally keeping a copy as `data_template` / `tracking_template` for a dashboard to be built against. Dry run unless you pass `--yes`; with no flags it only reports what is there. `--truncate` deletes rows below the header and **never the header itself** — `publish_gsheets` matches a parameter to a column by reading row 1, so a tab that lost it drops the next submission behind a 200. |
| `just launch` | Sends a flow execution to every number in a sample file. Credentials come from `.env` — never pass them on the command line, where they leak into shell history and the process table. |
| `just fetch` | Pulls executions from Twilio to reconcile against the published table. The warehouse is the database of record and can silently miss rows if publishing fails. |
| `just data-check FILE` | High-frequency checks on collected data: one observation per respondent, every row joinable back to the sampling frame, outcomes recorded. Meant to run on a loop during a live round. Findings are warnings — by the time data exists there is nothing left to prevent. |
| `just monitor --tracker FILE --hours N` | Watches a round land: one row per number, not per message. Each holds a state — `failed`, `sent`, `delivered`, `answered_back` — and the first two of those are final, so they stop being polled. Polls every 30 minutes (`--every`) and stops early once every number has settled — pass `--full-window` to keep polling for the whole `--hours` window instead, which is what a tab somebody is watching live needs. Reads the layer the other two cannot: a send Meta rejects never becomes an execution, and a reply Twilio could not hand over reads as `received` while the answer is gone. |
| `just decrypt` | Decrypts the encrypted columns of a collected dataset. |
| `just push` | Loads a dataset into MotherDuck. Appends by default; `--mode replace` **drops** the table. |
| `just cli ...` | Passes straight through to `rtt`, for anything without its own recipe. |

## Quality

| Recipe | What it does |
|---|---|
| `just test` | Runs the test suite. |
| `just test-cov` | Runs it with a coverage report. |
| `just lint-py` | `ruff check`. |
| `just fmt-python` / `just fmt-py FILE` | `ruff format`, everything or one file. |
| `just fmt-markdown` / `just fmt-md FILE` | markdownlint with `--fix`. |
| `just fmt-check-markdown` | markdownlint without fixing — what CI runs. |
| `just fmt-all` | `lint-py`, then `fmt-python`, then `fmt-markdown`. |
| `just pre-commit-run` | Runs the pre-commit hooks. |
| `just scan-secrets` | Scans tracked files for private keys. Only private keys: full gitleaks scanning runs in CI instead, because IPA-managed Windows blocks the gitleaks binary under Application Control. |
