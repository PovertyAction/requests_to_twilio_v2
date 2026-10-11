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
header-motherduck FILE *ARGS:
    uv run rtt flow table {{ FILE }} {{ ARGS }}

[doc("Write its Google Sheet header row, or add the columns it is missing")]
header-sheets FILE *ARGS:
    uv run rtt flow header {{ FILE }} {{ ARGS }}

# The names before the destination was in them, still used by the Twilio lab decks.
alias table := header-motherduck
alias header := header-sheets
