# Write a survey and build it into a flow. What each recipe is for, and why: docs/recipes/survey.md

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set working-directory := ".."

[private]
default:
    @just --unsorted --list survey

[doc("Open the survey builder, with your approved messages to pick from")]
builder:
    uv run rtt survey builder

[doc("Start a survey workbook from the documented example")]
new *ARGS:
    uv run rtt survey template {{ ARGS }}

[doc("Write the Excel workbook from a survey's JSON")]
excel FILE:
    uv run rtt survey convert {{ FILE }} {{ without_extension(FILE) }}.xlsx

[doc("Bring a workbook's edits back into the JSON")]
json FILE:
    uv run rtt survey convert {{ FILE }} {{ without_extension(FILE) }}.json

[doc("Read the whole survey in the terminal")]
rows FILE *ARGS:
    uv run rtt survey rows {{ FILE }} {{ ARGS }}

[doc("Check a survey before it becomes a flow")]
check FILE:
    uv run rtt survey check {{ FILE }}

[doc("Create its buttons and lists on Twilio (no approval needed)")]
content FILE *ARGS:
    uv run rtt survey content {{ FILE }} {{ ARGS }}

[doc("Compile it into a Studio flow, checked: --lang en")]
build FILE *ARGS:
    uv run rtt survey build {{ FILE }} {{ ARGS }}
