# What came back from a round. What each recipe is for, and why: docs/recipes/data.md

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set working-directory := ".."

[private]
default:
    @just --unsorted --list data

[doc("Run high-frequency checks on collected data")]
check FILE *ARGS:
    uv run rtt data-check {{ FILE }} {{ ARGS }}

[doc("Decrypt collected responses with your private key")]
decrypt *ARGS:
    uv run rtt decrypt {{ ARGS }}

[doc("Pull executions from Twilio, to find a row that seems missing")]
fetch *ARGS:
    uv run rtt fetch {{ ARGS }}

[doc("Load a dataset into MotherDuck (appends; --mode replace drops the table)")]
push *ARGS:
    uv run rtt push {{ ARGS }}
