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

# Clean venv
clean:
    rm -rf .venv

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

# Check for private keys in tracked files. Full gitleaks scanning runs in CI,
# because IPA-managed Windows blocks the gitleaks binary (Application Control).
scan-secrets:
    uv run pre-commit run detect-private-key --all-files

# Generate .claude/settings.local.json from the credentials in .env, so the
# Twilio MCP server can resolve the ${...} placeholders in .mcp.json.
mcp-setup:
    uv run python scripts/mcp_settings.py

# Show which MCP servers this project defines, and whether each is ready
mcp-list:
    uv run python scripts/mcp_settings.py --list

# List WhatsApp templates and their Meta approval status
template-list *ARGS:
    uv run rtt template list {{ ARGS }}

# Create a template in Twilio from templates/<name>.json. Does NOT submit to
# Meta - the wording can still be changed at this stage.
template-create FILE *ARGS:
    uv run rtt template create {{ FILE }} {{ ARGS }}

# Submit a template to Meta for WhatsApp approval. IRREVERSIBLE: submitted
# templates can never be edited, so review the wording first.
template-submit NAME *ARGS:
    uv run rtt template submit {{ NAME }} {{ ARGS }}

# Delete an unsubmitted template so its wording can be redone. Twilio has no
# update operation for content, so this is how a draft gets revised. Refuses
# anything already submitted to Meta.
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

# Pull executions from Twilio to reconcile against the Google Sheet.
fetch *ARGS:
    uv run rtt fetch {{ ARGS }}

# Load a dataset into MotherDuck.
push *ARGS:
    uv run rtt push {{ ARGS }}

[windows]
pre-install:
    winget install Casey.Just astral-sh.uv GitHub.cli OpenJS.NodeJS
    npm install -g markdownlint-cli2

[linux]
pre-install:
    brew install just uv gh markdownlint-cli2

[macos]
pre-install:
    brew install just uv gh markdownlint-cli2

# List Studio flows
flow-list:
    uv run rtt flow list

# Save a flow definition into flows/ so it can be reviewed and diffed
flow-pull NAME *ARGS:
    uv run rtt flow pull {{ NAME }} {{ ARGS }}

# High-frequency checks for flows: verify the instrument was coded correctly.
# Omit NAME to check every flow on the account.
flow-check *ARGS:
    uv run rtt flow check {{ ARGS }}

# Build the data-use demo flow in both languages, plus the content templates
# its interactive questions need. Pass --lang en or --lang es for just one.
build-demo-flow *ARGS:
    uv run python scripts/build_data_use_demo.py {{ ARGS }}

# Create every content template the demo flow needs that does not exist yet.
# These are in-session only (quick-reply buttons and list pickers), so none of
# them is ever submitted to Meta - only the two bookends need approval. Safe to
# re-run: --skip-existing leaves a template that is already there alone rather
# than creating a second one with the same name.
#
# The directory is passed to the CLI rather than looped over here: a shell loop
# needs a bash shebang, which `just` cannot run on Windows without cygpath.
demo-templates-create:
    uv run rtt template create templates/generated --skip-existing --yes

# Deploy encrypt_fields.js and publish_motherduck.js as Twilio Functions
deploy-functions:
    uv run python scripts/deploy_twilio_functions.py

# Deploy a flow definition. Refuses to ship one that fails the checks.
flow-deploy FILE *ARGS:
    uv run rtt flow deploy {{ FILE }} {{ ARGS }}
