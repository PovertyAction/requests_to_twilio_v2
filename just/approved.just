# Approved messages: the ones Meta reviews. What each recipe is for, and why: docs/recipes/approved.md

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set working-directory := ".."

[private]
default:
    @just --unsorted --list approved

[doc("List approved messages and their status")]
list *ARGS:
    uv run rtt template list {{ ARGS }}

[doc("Create one from templates/<name>.json (not yet sent to Meta)")]
create FILE *ARGS:
    uv run rtt template create {{ FILE }} {{ ARGS }}

[doc("Submit to Meta for approval (irreversible: the wording is then fixed)")]
submit NAME *ARGS:
    uv run rtt template submit {{ NAME }} {{ ARGS }}

[doc("Show whether Meta has approved it")]
status NAME:
    uv run rtt template status {{ NAME }}

[doc("Delete one not yet submitted, to redo its wording")]
delete NAME *ARGS:
    uv run rtt template delete {{ NAME }} {{ ARGS }}
