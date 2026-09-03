# Every recipe in the Justfile

`just` is the entry point to this repo. `just --list` prints the recipes; this
page says what each one is for and when you would reach for it.

Recipes are grouped by the order you meet them, not alphabetically. If you are
new here, read [START-HERE.md](../START-HERE.md) first — it walks a round end to
end, and this page is the reference you come back to.

**There is no recipe that turns a survey spec into a Studio flow.** `rtt survey`
reads, checks and converts an instrument between JSON and Excel; the only thing
that emits a flow definition is `just build-demo-flow`, from its own Python
tables in `scripts/build_data_use_demo.py`. Changing the demo's questions means
editing that script - it is the only description of that instrument.

Each recipe's *reasoning* lives in the Justfile comment above it, which
`just --show RECIPE` prints. This page is the index; [running a
round](running-a-round.md) is the walkthrough.

## Setting up

Run these once, when you clone the repo or when the toolchain changes.

| Recipe | What it does |
|---|---|
| `just` | Lists every recipe. The default when you type `just` with no arguments. |
| `just pre-install` | Installs uv, gh, Node and markdownlint. Node is not optional — the interop test runs real Twilio JavaScript. |
| `just get-started` | `pre-install` then `venv`. The one command for a fresh machine. |
| `just venv` | Syncs dependencies with uv and installs the pre-commit hooks. |
| `just clean` | Deletes `.venv`. |
| `just update-reqs` | Upgrades the lockfile, re-syncs, and updates pre-commit hooks. |
| `just system-info` | Prints CPU, OS family and OS. Useful when a bug looks platform-specific. |
| `just mcp-setup` | Writes Twilio MCP credentials from `.env` into gitignored `.claude/settings.local.json`. Restart Claude Code, then `/mcp` to confirm. |
| `just mcp-list` | Shows which MCP servers the project defines and whether each is ready. |

## Writing an instrument

A Studio flow is 97 widgets for 6 questions and nobody can review that. The spec
is the same instrument as ~20 rows, in the shape SurveyCTO users already know.

**The JSON is canonical** — it is what git carries and what a reviewer diffs. The
workbook is a generated view, gitignored, and it is what you edit.

| Recipe | What it does |
|---|---|
| `just survey-template` | Writes a documented starter workbook to fill in — a small working instrument with one row of every type, not an empty sheet. Refuses to overwrite. |
| `just survey-xlsx FILE` | Regenerates the editable workbook from canonical JSON. Overwrites: the workbook is a view, not a document with a history. |
| `just survey-json FILE` | Reads an RA's workbook edits back into the tracked JSON. **The load-bearing direction** — whatever does not survive this trip is silently lost. |
| `just survey-rows FILE` | Prints the whole instrument in the terminal, without opening Excel. |
| `just survey-check FILE` | *Runs* the option patterns and constraints rather than reading them, and reports where each possible reply lands. Exits non-zero. |
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

Content templates are **immutable**, and changing only a question's wording
fails silently - see [writing templates](writing-templates.md#templates-are-immutable).

## Building and shipping a flow

| Recipe | What it does |
|---|---|
| `just build-demo-flow` | Builds both languages from one structure. `--lang en` for one; `--publish-target gsheets` or `motherduck` picks the destination, gsheets if unnamed. Needs live credentials — templates resolve by name. |
| `just flow-check [FILE]` | The instrument-side equivalent of high-frequency checks: verifies the survey was coded correctly. Omit the file to check every flow on the account. Exits non-zero on an error. |
| `just flow-schema FILE` | Prints `CREATE TABLE` DDL matching what the flow publishes. **Run this after changing the instrument** — the publish Function only inserts into columns that already exist, so a new question with no column is dropped silently behind a 200. |
| `just flow-header FILE` | The Google Sheets counterpart: prints the header row for row 1 of the target sheet. Same reason, same failure — `publish_gsheets` matches parameters to columns by header name. |
| `just flow-deploy FILE` | Deploys a definition. Refuses to ship one that fails the checks. |
| `just flow-list` | Lists the Studio flows on the account. |
| `just flow-pull NAME` | Saves a flow definition into `flows/` (gitignored) to review, diff, or hand to an agent. Refuses to write one containing credentials. |
| `just deploy-functions` | Deploys `encrypt_fields.js` and **both** publish Functions — a deployment is the whole set, so a subset removes the rest. Missing credentials for one target are fine; none is an error. |

## Running a round

| Recipe | What it does |
|---|---|
| `just keygen` | Generates the X25519 keypair. The public key goes to Twilio; the private key stays on your machine and is gitignored. |
| `just intake BUILDER` | Turns an external export into a validated launch sample: E.164 against the country collected, consent gate, no landlines, `caseid`/`arm` frozen across re-runs. Unresolvable rows go to `<out>_needs_human_review.csv`. |
| `just send SAMPLE COLUMNS` | Sends, **then watches for an hour**, mirroring every two minutes into the `tracking` tab — or a MotherDuck table with `just watch=motherduck send ...`. `--resume` is baked in, so re-running sends only to new sign-ups. A dry run starts no tracker. |
| `just round-reset SAMPLE` | Clears the `data` and `tracking` tabs, `--snapshot` keeping template copies. Dry run unless `--yes`. Never deletes a header row — `publish_gsheets` reads row 1 to map columns. |
| `just launch` | Sends a flow execution to every number in a sample file. Credentials come from `.env` — never pass them on the command line, where they leak into shell history and the process table. |
| `just fetch` | Pulls executions from Twilio to reconcile against the published table. The warehouse is the database of record and can silently miss rows if publishing fails. |
| `just data-check FILE` | One observation per respondent, every row joinable to the frame, outcomes recorded. Warnings only — by the time data exists there is nothing left to prevent. |
| `just monitor --tracker FILE --hours N` | One row per number: `failed`, `sent`, `delivered`, `answered_back`. Stops once all settle — `--full-window` keeps a live view moving. `--sheet` or `--table` mirrors each poll. Reads the layer `fetch` and `data-check` cannot. |
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
| `just scan-secrets` | Scans tracked files for private keys only. Full gitleaks runs in CI — Application Control blocks the binary on IPA Windows. |
