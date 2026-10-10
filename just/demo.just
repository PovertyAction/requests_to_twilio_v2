# The shipped data-use demo. What each recipe is for, and why: docs/recipes/demo.md

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set working-directory := ".."

[private]
default:
    @just --unsorted --list demo

[doc("Build the demo flow: --lang en, --publish-target motherduck")]
build *ARGS:
    uv run python scripts/build_data_use_demo.py {{ ARGS }}

[doc("Create the demo's buttons and lists that do not exist yet")]
content:
    uv run rtt template create templates/generated --skip-existing --yes

[doc("Recreate any of them whose wording changed in the repo")]
content-sync:
    uv run rtt template create templates/generated --replace --yes
