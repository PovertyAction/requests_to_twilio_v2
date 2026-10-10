# Put a flow on Twilio and look inside it. What each recipe is for, and why: docs/recipes/flow.md

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set working-directory := ".."

[private]
default:
    @just --unsorted --list flow

[doc("Deploy a flow, as a draft unless --publish (refuses one that fails its checks)")]
deploy FILE *ARGS:
    uv run rtt flow deploy {{ FILE }} {{ ARGS }}

[doc("Check a flow for coding defects; with no file, every flow on the account")]
check *ARGS:
    uv run rtt flow check {{ ARGS }}

[doc("List the account's Studio flows")]
list:
    uv run rtt flow list

[doc("Save a flow from the account into flows/, to read or diff it")]
pull NAME *ARGS:
    uv run rtt flow pull {{ NAME }} {{ ARGS }}

[doc("Create its MotherDuck table, or add the columns it is missing")]
table FILE *ARGS:
    uv run rtt flow table {{ FILE }} {{ ARGS }}

[doc("Print the header row for its Google Sheet")]
header FILE:
    uv run rtt flow schema {{ FILE }} --format header
