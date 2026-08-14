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
`scripts/build_data_use_demo.py`. `just export-demo-spec` exists to lift those
tables into spec form and will be retired once the compiler is written. Until
then, changing the demo's questions means editing that script.

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

A Studio flow is 73 widgets for 8 questions and nobody can review that. The spec
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
| `just export-demo-spec` | One-off: lifts the demo flow's Python tables into spec form. Will be retired. |

The editing loop:

```sh
just survey-xlsx surveys/data_use_demo.json    # make the workbook
# ...edit it in Excel...
just survey-json surveys/data_use_demo.xlsx    # bring the edits back
just survey-check surveys/data_use_demo.json   # then commit the JSON
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

> **Content templates are immutable.** `demo-templates-create` uses
> `--skip-existing`, which leaves an existing template alone rather than making a
> duplicate. That is what you want when re-running a build — and exactly what you
> do **not** want after changing a question's wording or options, because the
> flow resolves templates *by friendly name*. The old content would be found,
> the flow would send the old question, and the new split would refuse every
> answer. After changing a question: `just template-delete` it, then recreate.

## Building and shipping a flow

| Recipe | What it does |
|---|---|
| `just build-demo-flow` | Builds the demo flow and the templates its questions need, both languages from one structure and two string tables. `--lang en` for one. Needs live Twilio credentials: content templates are looked up by name. |
| `just flow-check [FILE]` | The instrument-side equivalent of high-frequency checks: verifies the survey was coded correctly. Omit the file to check every flow on the account. Exits non-zero on an error. |
| `just flow-schema FILE` | Prints `CREATE TABLE` DDL matching what the flow publishes. **Run this after changing the instrument** — the publish Function only inserts into columns that already exist, so a new question with no column is dropped silently behind a 200. |
| `just flow-deploy FILE` | Deploys a definition. Refuses to ship one that fails the checks. |
| `just flow-list` | Lists the Studio flows on the account. |
| `just flow-pull NAME` | Saves a flow definition into `flows/` so it can be reviewed and diffed. |
| `just deploy-functions` | Deploys `encrypt_fields.js` and `publish_motherduck.js` as Twilio Functions. |

## Running a round

| Recipe | What it does |
|---|---|
| `just keygen` | Generates the X25519 keypair. The public key goes to Twilio; the private key stays on your machine and is gitignored. |
| `just launch` | Sends a flow execution to every number in a sample file. Credentials come from `.env` — never pass them on the command line, where they leak into shell history and the process table. |
| `just fetch` | Pulls executions from Twilio to reconcile against the published table. The warehouse is the database of record and can silently miss rows if publishing fails. |
| `just data-check FILE` | High-frequency checks on collected data: one observation per respondent, every row joinable back to the sampling frame, outcomes recorded. Meant to run on a loop during a live round. Findings are warnings — by the time data exists there is nothing left to prevent. |
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
