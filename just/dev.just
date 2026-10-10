# Maintaining the repo. What each recipe is for, and why: docs/recipes/dev.md

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set working-directory := ".."

[private]
default:
    @just --unsorted --list dev

[doc("Run the tests")]
test *ARGS:
    uv run pytest {{ ARGS }}

[doc("Run the tests with a coverage report")]
cov:
    uv run pytest --cov=requests_to_twilio --cov-report=term-missing

[doc("Lint the Python")]
lint:
    uv run ruff check

[doc("Format the Python and the markdown")]
fmt:
    uv run ruff format
    markdownlint-cli2 --config .markdownlint.yaml "**/*.{md,qmd}" "#.venv" --fix

[doc("Run the pre-commit hooks on what is staged")]
pre-commit:
    uv run pre-commit run

[doc("Scan tracked files for private keys")]
secrets:
    uv run pre-commit run detect-private-key --all-files

[doc("Upgrade the locked dependencies and the hooks")]
update:
    uv lock --upgrade
    uv sync
    uv run pre-commit autoupdate

[doc("Rewrite the builder's embedded limits and starter from the Python side")]
builder-sync:
    uv run python -c "from requests_to_twilio.builder import write_contract; print(write_contract())"

[doc("Regenerate the committed sample_template.xlsx")]
sample:
    uv run python -c "import pathlib; pathlib.Path('sample_template.xlsx').unlink(missing_ok=True)"
    uv run rtt survey template -o sample_template.xlsx

[doc("Delete the virtual environment")]
[windows]
clean:
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .venv

[doc("Delete the virtual environment")]
[unix]
clean:
    rm -rf .venv
