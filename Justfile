# Set shell for Windows

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Set path to virtual environment's python

venv_dir := ".venv"
python := venv_dir + if os_family() == "windows" { "/Scripts/python.exe" } else { "/bin/python3" }

# List available recipes
default:
    @just --list

# Display system information
system-info:
    @echo "CPU architecture: {{ arch() }}"
    @echo "Operating system type: {{ os_family() }}"
    @echo "Operating system: {{ os() }}"

# Delete the virtual environment
[windows]
clean:
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue {{ venv_dir }}

# Delete the virtual environment
[unix]
clean:
    rm -rf {{ venv_dir }}

# Setup environment
get-started: pre-install venv

# Update project software versions in requirements
update-reqs:
    uv lock --upgrade
    uv sync
    pre-commit autoupdate

# create virtual environment
venv:
    uv sync
    uv tool install pre-commit
    uv run pre-commit install

# Lint python code
lint-py:
    uv run ruff check

# Format python code
fmt-python:
    uv run ruff format

# Format a single python file, "f"
fmt-py f:
    uv run ruff format {{ f }}

# Format all markdown and config files
fmt-markdown:
    markdownlint-cli2 --config {{ justfile_directory() }}/.markdownlint.yaml "**/*.{md,qmd}" "#.venv" --fix

# Format a single markdown file, "f"
fmt-md f:
    markdownlint-cli2 --config {{ justfile_directory() }}/.markdownlint.yaml {{ f }} --fix

# Check format of all markdown files
fmt-check-markdown:
    markdownlint-cli2 --config {{ justfile_directory() }}/.markdownlint.yaml "**/*.{md,qmd}" "#.venv"

# Lint and format everything: python then markdown
fmt-all: lint-py fmt-python fmt-markdown

# Run pre-commit hooks
pre-commit-run:
    pre-commit run

# Run the test suite
test:
    uv run pytest

# Run the test suite with a coverage report
test-cov:
    uv run pytest --cov=requests_to_twilio --cov-report=term-missing

# Full gitleaks scanning runs in CI instead, because IPA-managed Windows blocks
# the gitleaks binary under Application Control. This catches private keys only.
[doc("Scan tracked files for private keys")]
scan-secrets:
    uv run pre-commit run detect-private-key --all-files

# .mcp.json is committed and shared, so it names credentials as ${VAR} rather
# than holding them. This writes them to .claude/settings.local.json, which is
# gitignored. Restart Claude Code afterwards, then run /mcp to confirm.
[doc("Write the Twilio MCP credentials from .env into Claude Code settings")]
mcp-setup:
    uv run python scripts/mcp_settings.py

# Show which MCP servers this project defines, and whether each is ready
mcp-list:
    uv run python scripts/mcp_settings.py --list

# List WhatsApp templates and their Meta approval status
template-list *ARGS:
    uv run rtt template list {{ ARGS }}

# Creates the template in Twilio but does NOT submit it to Meta, so the wording
# can still be changed at this stage.
[doc("Create a WhatsApp template from templates/<name>.json")]
template-create FILE *ARGS:
    uv run rtt template create {{ FILE }} {{ ARGS }}

# IRREVERSIBLE. A submitted template can never be edited - only deleted and
# recreated under a new name - so review the wording before running this.
[doc("Submit a template to Meta for WhatsApp approval (IRREVERSIBLE)")]
template-submit NAME *ARGS:
    uv run rtt template submit {{ NAME }} {{ ARGS }}

# Twilio has no update operation for content, so deleting is how a draft gets
# revised. Refuses anything already submitted to Meta.
[doc("Delete an unsubmitted template so its wording can be redone")]
template-delete NAME *ARGS:
    uv run rtt template delete {{ NAME }} {{ ARGS }}

# Check a template's approval status
template-status NAME:
    uv run rtt template status {{ NAME }}

# Show the CLI help
cli *ARGS:
    uv run rtt {{ ARGS }}

# Generate the X25519 keypair. Public key goes to Twilio, private key stays here.
keygen *ARGS:
    uv run rtt keygen {{ ARGS }}

# Launch a survey. Credentials come from .env - never pass them on the command line.
launch *ARGS:
    uv run rtt launch {{ ARGS }}

# Decrypt collected responses.
decrypt *ARGS:
    uv run rtt decrypt {{ ARGS }}

# Pull executions from Twilio to reconcile against the published table
fetch *ARGS:
    uv run rtt fetch {{ ARGS }}

# Load a dataset into MotherDuck. Appends by default; --mode replace DROPS
[doc("Load a dataset into MotherDuck")]
push *ARGS:
    uv run rtt push {{ ARGS }}

# Node is not optional and was missing from the non-Windows recipes: the
# cross-language interop test runs the real Twilio JavaScript, and the Twilio
# MCP server is launched with npx. (`just` itself is already present - you are
# running it.)

# Install the toolchain: uv, gh, Node, markdownlint
[windows]
pre-install:
    winget install Casey.Just astral-sh.uv GitHub.cli OpenJS.NodeJS
    npm install -g markdownlint-cli2

# Install the toolchain: uv, gh, Node, markdownlint
[linux]
pre-install:
    sudo apt-get update
    sudo apt-get install -y nodejs npm gh
    curl -LsSf https://astral.sh/uv/install.sh | sh
    npm install -g markdownlint-cli2

# Install the toolchain: uv, gh, Node, markdownlint
[macos]
pre-install:
    brew install uv gh node markdownlint-cli2

# List Studio flows
flow-list:
    uv run rtt flow list

# Save a flow definition into flows/ so it can be reviewed and diffed
flow-pull NAME *ARGS:
    uv run rtt flow pull {{ NAME }} {{ ARGS }}

# The instrument-side equivalent of high-frequency checks: it verifies the
# survey was coded correctly. Omit NAME to check every flow on the account.
[doc("Check a flow for coding defects. Exits non-zero on an error")]
flow-check *ARGS:
    uv run rtt flow check {{ ARGS }}

# Builds both languages from one structure and two string tables, so a fix to
# the graph lands in both. Pass --lang en or --lang es for just one. Needs live
# Twilio credentials: the content templates are looked up by name.
#
# --publish-target chooses where a submission is written. Both destinations are
# deployed by `just deploy-functions`, so switching is a rebuild of the flow
# rather than a redeployment of the account:
#
#   just build-demo-flow "--lang en"                          # MotherDuck
#   just build-demo-flow "--lang en --publish-target gsheets" # Google Sheets
#
# The flow file is written to the same path either way, so `git diff flows/`
# after switching shows exactly one widget changing - which is the review.
[doc("Build the data-use demo flow and the templates its questions need")]
build-demo-flow *ARGS:
    uv run python scripts/build_data_use_demo.py {{ ARGS }}

# These are in-session only (quick-reply buttons and list pickers), so none is
# ever submitted to Meta - only the two bookends need approval. Safe to re-run:
# --skip-existing leaves an existing template alone rather than creating a
# second one with the same name.
#
# The directory is passed to the CLI rather than looped over here: a shell loop
# needs a bash shebang, which `just` cannot run on Windows without cygpath.
[doc("Create the demo flow's in-session content templates")]
demo-templates-create:
    uv run rtt template create templates/generated --skip-existing --yes

# Make Twilio match the repo after any wording change.
#
# `demo-templates-create` is the wrong tool for that and quietly so: Twilio has
# no update operation for content, and --skip-existing leaves the old wording in
# place. The flow references a template by SID, so the stale text is invisible
# to `flow-check`, to the linter and to the tests, and reaches the respondent.
#
# That is not hypothetical. Adding a sixth question renumbered every body to
# "of 6"; four templates already existed, kept saying "of 5", and a live
# respondent was asked six questions numbered out of five. Nothing caught it
# except reading her transcript.
#
# --replace deletes and recreates only what has actually drifted, and refuses
# outright on anything submitted to Meta - approval attaches to the SID, so
# replacing an approved template throws the approval away and the next round
# fails at send time.
[doc("Replace any content template whose wording has drifted from the repo")]
demo-templates-sync:
    uv run rtt template create templates/generated --replace --yes

# Deploys encrypt_fields.js and BOTH publish functions. A Twilio deployment is
# the complete set of functions in its build, so deploying a subset removes the
# others rather than leaving them alone - and every flow pointing at one starts
# 404ing mid-execution. Each target's credentials are optional and separately
# reported; only having none at all is an error.
[doc("Deploy the encryption and publish Functions to Twilio")]
deploy-functions:
    uv run python scripts/deploy_twilio_functions.py

# Deploy a flow definition. Refuses to ship one that fails the checks.
flow-deploy FILE *ARGS:
    uv run rtt flow deploy {{ FILE }} {{ ARGS }}

# The publish Function only inserts into columns that already exist, so a
# question added to the flow with no matching column is dropped silently behind
# a 200. Run this after changing the instrument, and apply the difference.
[doc("Print CREATE TABLE DDL matching what a flow publishes")]
flow-schema FILE *ARGS:
    uv run rtt flow schema {{ FILE }} {{ ARGS }}

# The Google Sheets counterpart of flow-schema, and it exists for the same
# reason: publish_gsheets maps a parameter to a column by matching its name
# against row 1, so a question whose name is not in the header row is dropped
# behind a 200 and a row that looks complete. Paste the output into row 1 of the
# target sheet rather than typing thirty column names by hand.
[doc("Print the spreadsheet header row matching what a flow publishes")]
flow-header FILE:
    uv run rtt flow schema {{ FILE }} --format header

# One observation per respondent, every row joinable back to the sampling frame,
# outcomes recorded. Meant to be run on a loop during a live round, so findings
# are warnings: by the time data exists there is nothing left to prevent. The
# instrument-side equivalent is `just flow-check`.
[doc("Run high-frequency checks on collected data")]
data-check FILE *ARGS:
    uv run rtt data-check {{ FILE }} {{ ARGS }}

# The layer `fetch` and `data-check` cannot see. A send Meta rejects never
# becomes an execution and never publishes a row, so the respondent is absent
# from the data rather than incomplete in it - and a reply Twilio could not hand
# to the flow reads as `received` everywhere while the answer is gone.
#
# One row per number, not per message: failed / sent / delivered / answered_back.
# The first and last are final and stop being polled, so the loop ends by itself
# once every number has settled rather than spending rate limit on a done round.
#
#   just monitor "--tracker sample_output.csv --hours 4"
[doc("Watch a round land, one row per number")]
monitor *ARGS:
    uv run rtt monitor {{ ARGS }}

# A Studio flow is 73 widgets for 8 questions and nobody can review it. The spec
# is the same instrument as ~20 rows, in the shape SurveyCTO users already know:
# one row is one question AND the whole subgraph it becomes.
#
# The JSON is canonical - it is what git carries and what a reviewer diffs. The
# workbook is a generated view, gitignored, and it is what you edit.
#
#   just survey-template                  # start from the documented example
#   just survey-xlsx my_survey.json       # make the workbook
#   ...edit it in Excel...
#   just survey-json my_survey.xlsx       # bring the edits back
#   just survey-check my_survey.json      # then commit the JSON

# Read the whole instrument in the terminal, without Excel
survey-rows FILE *ARGS:
    uv run rtt survey rows {{ FILE }} {{ ARGS }}

# The instrument-side equivalent of XLSForm validation: it does not read the
# option patterns and constraints, it RUNS them, and reports where each possible
# reply lands. Exits non-zero on a problem, so it can gate a build.
[doc("Check a survey spec before it becomes a flow")]
survey-check FILE:
    uv run rtt survey check {{ FILE }}

# Regenerate the workbook from the canonical JSON. Overwrites it: the workbook is
# a view, not a document with a history.
[doc("Write the editable workbook from a spec's JSON")]
survey-xlsx FILE:
    uv run rtt survey convert {{ FILE }} {{ without_extension(FILE) }}.xlsx

# Bring an RA's edits back into the tracked JSON. This is the load-bearing
# direction: what does not survive the trip back is silently lost.
[doc("Read a workbook's edits back into the canonical JSON")]
survey-json FILE:
    uv run rtt survey convert {{ FILE }} {{ without_extension(FILE) }}.json

# `export-demo-spec` was retired here. It lifted the demo flow's Python tables
# into spec form, and its output was committed as surveys/data_use_demo.json - a
# published-looking description of the instrument that went stale three hours
# after it was written and then could not be regenerated, because the exporter
# was never updated when the instrument gained a fifth question and an integer
# type. Two descriptions of one instrument, and the wrong one was the readable
# one. scripts/build_data_use_demo.py is now the only description of the demo.

# A documented starter workbook to fill in - a small working instrument showing
# one row of every type, not an empty sheet. Writes survey.xlsx by default and
# refuses to overwrite, so it cannot clobber an instrument mid-edit.
[doc("Create a new survey workbook from the starter template")]
survey-template *ARGS:
    uv run rtt survey template {{ ARGS }}

# Regenerate the committed reference workbook. Only needed when the schema or the
# starter content changes - a test fails when it has drifted, because a stale
# sample teaches the format it was generated from rather than the one in the code.
[doc("Regenerate the committed sample_template.xlsx")]
survey-sample:
    uv run python -c "import pathlib; pathlib.Path('sample_template.xlsx').unlink(missing_ok=True)"
    uv run rtt survey template -o sample_template.xlsx
