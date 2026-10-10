# Once per laptop and per Twilio account. What each recipe is for, and why: docs/recipes/setup.md

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set working-directory := ".."

[private]
default:
    @just --unsorted --list setup

[doc("Install the toolchain and the project (uv, gh, Node, markdownlint)")]
install: _toolchain
    uv sync
    uv tool install pre-commit
    uv run pre-commit install

[doc("Generate the encryption keypair: the public half goes to Twilio")]
key *ARGS:
    uv run rtt keygen {{ ARGS }}

[doc("Deploy the encryption and publish Functions to Twilio")]
functions:
    uv run python scripts/deploy_twilio_functions.py

[doc("Give Claude Code the Twilio MCP credentials from .env")]
mcp:
    uv run python scripts/mcp_settings.py

[doc("Show which MCP servers are ready")]
mcp-check:
    uv run python scripts/mcp_settings.py --list

[private]
[windows]
_toolchain:
    winget install Casey.Just astral-sh.uv GitHub.cli OpenJS.NodeJS
    npm install -g markdownlint-cli2

[private]
[linux]
_toolchain:
    sudo apt-get update
    sudo apt-get install -y nodejs npm gh
    curl -LsSf https://astral.sh/uv/install.sh | sh
    npm install -g markdownlint-cli2

[private]
[macos]
_toolchain:
    brew install just uv gh node markdownlint-cli2
