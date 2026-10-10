# Send a round and watch it land. What each recipe is for, and why: docs/recipes/round.md

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set working-directory := ".."

[private]
default:
    @just --unsorted --list round

[doc("Turn an export into a checked launch sample, with a builder script")]
intake BUILDER *ARGS:
    uv run python {{ BUILDER }} {{ ARGS }}

[arg("watch", long="watch")]
[arg("dry_run", long="dry-run", value="--dry-run")]
[doc("Send to SAMPLE.xlsx, then watch for an hour: --dry-run, --watch motherduck")]
send SAMPLE COLUMNS watch="sheet" dry_run="" *ARGS:
    @echo "Watching in {{ if watch == "sheet" { "a sheet" } else if watch == "motherduck" { "MotherDuck" } else { error("--watch is sheet or motherduck, not " + watch) } }} after sending."
    uv run rtt launch {{ SAMPLE }}.xlsx --columns {{ COLUMNS }} --resume {{ dry_run }} {{ ARGS }}
    {{ if dry_run != "" { "echo 'dry run, so no watcher started'" } else { "uv run rtt monitor --tracker " + SAMPLE + "_output.csv --sample " + SAMPLE + ".xlsx " + (if watch == "sheet" { "--sheet" } else { "--table tracking" }) + " --every 2 --hours 1 --full-window" } }}

[doc("Watch delivery, one row per number")]
watch *ARGS:
    uv run rtt monitor {{ ARGS }}

[doc("Clear a round's tabs and files (reports only, until --yes)")]
reset SAMPLE *ARGS:
    uv run python scripts/reset_round.py --sample {{ SAMPLE }} {{ ARGS }}

[doc("Launch with every option of rtt launch, for what send does not cover")]
launch *ARGS:
    uv run rtt launch {{ ARGS }}
