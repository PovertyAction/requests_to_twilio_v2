"""Clear a round's collected data, and keep a copy of it as a template.

A test round leaves rows in three places, and the day before a launch all three
have to be dealt with deliberately rather than left to be noticed later:

  * the `data` tab, one row per submission, written by the publish Function
  * the `tracking` tab, one row per respondent, rewritten by `rtt monitor`
  * this working directory, where the trackers, the exports and the sample
    workbooks accumulate - including one decrypted file, which is plain-text PII

Three operations, chosen independently so one command covers the whole day:

    just round-reset SAMPLE.xlsx                  # say what is there, change nothing
    just round-reset SAMPLE.xlsx "--snapshot"     # data -> data_template, and tracking
    just round-reset SAMPLE.xlsx "--truncate"     # live tabs back to their header row
    just round-reset SAMPLE.xlsx "--local old_output.csv"   # delete named leftovers
    just round-reset SAMPLE.xlsx "--snapshot --truncate --yes"

**Nothing happens without `--yes`.** Every operation here destroys something on
a live surface, and a dry run that prints row counts is the only way to be sure
the thing about to be emptied is the thing you meant.

`--snapshot` exists because a dashboard needs rows to be built against, and the
rows a rehearsal produced are the only honest sample of what the real round will
look like. It copies them somewhere the live tabs can then be emptied without
losing them, so the dashboard has real shapes to render and the live tabs start
the day at zero.

The form's response tab is never a target. Only the four tabs named below are
ever read or written, so a sign-up sheet living in the same workbook cannot be
caught by this.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# `sheets._request` is private, and used here on purpose. It is the module's one
# HTTP path, and it carries the remediation text for the two failures this
# script can actually provoke - a 403 that means the sheet was never shared with
# the service account, and a 400 that means the tab does not exist. Repeating
# the call with `requests` would lose both.
from requests_to_twilio import sheets

#: The live tabs, and where a snapshot of each goes. The dashboard is built
#: against the template and repointed at the live tab once it works.
SNAPSHOTS = {"data": "data_template", "tracking": "tracking_template"}

#: Every tab this script may touch. Anything else in the workbook - the sign-up
#: form's response tab above all - is out of reach by construction.
TOUCHABLE = frozenset(SNAPSHOTS) | frozenset(SNAPSHOTS.values())

#: Filename patterns that mean "collected data sitting in a working directory".
#: Used to *report* what is there, never to delete it: `--local` takes the names
#: explicitly, because a wildcard that deletes is a wildcard that deletes the
#: wrong thing once.
#:
#: This replaces a hardcoded tuple of one operator's rehearsal filenames -
#: `my_solo_test.xlsx`, `my_es_test.xlsx`, two dated files under
#: `.launch_archive/`. Every stranger running `just round-reset` was shown
#: filenames from somebody else's laptop and told they were "local test
#: artifacts", and `--local` then deleted by those names from whatever working
#: directory they happened to be in. A default here is somebody else's round.
#:
#: The current round's own files are round_files() below, and the split is not
#: cosmetic: what this finds is old and safe to remove at any time, that one is
#: current state and only safe to remove between rounds.
DATA_SHAPED = ("*_output.csv", "*_decrypted.csv", "*_export.csv", "*.xlsx")


#: The working files of the round that just ran. Deleting these is what makes
#: the *next* round start clean, and skipping it does not merely leave clutter -
#: it silently mixes two rounds:
#:
#:   * The sample carries the last round's caseids, and `build` deliberately
#:     carries a known number's caseid and arm forward. So a rehearsal number
#:     that signs up for real keeps its rehearsal caseid - which the tracker has
#:     already recorded as sent, so `--resume` skips it and that person never
#:     gets the real message.
#:   * The tracker is where `rtt monitor` reads a round's start time from, so a
#:     stale one widens the poll window back to the previous launch and pulls its
#:     messages into this round's tracking tab.
#:   * The delivery log is merged into, never replaced, and a settled row is
#:     never walked back - so last round's rows persist into this round's tab.
#:
#: All three are gitignored. Deliberately separate from --local: those are old
#: junk, these are current state, and one of them is the file `just send` reads.
#:
#: Derived from whichever sample the round is launching, because a round is
#: named by its sample and everything else follows: `rtt launch x.xlsx` writes
#: `x_output.csv`, and the delivery log is shared by every round because the
#: monitor merges into it. Naming the files here instead would bind this script
#: to one round, which is how the previous version shipped with `rst2026_` in it.
def round_files(sample: str) -> tuple[str, ...]:
    """Return the three files that carry state from one round into the next.

    Both the directory and the real suffix have to survive. `rtt launch` writes
    the tracker *beside* the sample rather than in the working directory, and it
    accepts `.xlsx`, `.xlsm` and `.csv` samples alike. Deriving from the bare
    stem was wrong twice over: `--sample rounds/today.xlsx` deleted an unrelated
    `today.xlsx` in the working directory while reporting the real tracker as
    absent, and `--sample panel.csv` named a `panel.xlsx` that never existed.
    Both failures leave the round's real state in place, which is the thing this
    command exists to remove.

    A bare name with no suffix is taken as `.xlsx`, because that is the form
    `just send` uses.
    """
    path = Path(sample)
    workbook = path if path.suffix else path.with_suffix(".xlsx")
    tracker = workbook.with_name(f"{workbook.stem}_output.csv")
    return (str(workbook), str(tracker), "delivery_log.csv")


#: Never deleted. Committed reference material that ships with the toolkit
#: rather than data from a round. The round's own input - a sign-up export
#: somebody maintains by hand - is added to this at run time from --signups.
KEEP = ("sample_input.xlsx", "sample_template.xlsx")


class ResetError(Exception):
    """Raised when the workbook cannot be read, or a tab is not safe to touch."""


def tabs(sheet_id: str, token: str) -> dict[str, dict]:
    """Return ``{title: properties}`` for every tab in the workbook."""
    meta = sheets._request(
        "GET",
        f"{sheets.SHEETS_API}/{sheet_id}",
        token,
        params={"fields": "properties.title,sheets.properties"},
    )
    return {s["properties"]["title"]: s["properties"] for s in meta.get("sheets", [])}


def values(sheet_id: str, token: str, tab: str) -> list[list[str]]:
    """Every value in a tab, header row first."""
    payload = sheets._request(
        "GET",
        f"{sheets.SHEETS_API}/{sheet_id}/values/{sheets.quote_tab(tab)}",
        token,
    )
    return payload.get("values", [])


def guard(tab: str) -> None:
    """Refuse any tab this script has no business touching."""
    if tab not in TOUCHABLE:
        raise ResetError(
            f"Refusing to touch tab {tab!r}: this command only ever reads or "
            f"writes {', '.join(sorted(TOUCHABLE))}."
        )


def truncate(sheet_id: str, token: str, tab: str, properties: dict) -> int:
    """Delete every row below the header, and return how many went.

    One `deleteDimension` rather than clear-then-rewrite-the-header. The
    difference matters: a clear that succeeds and a write that then fails leaves
    a tab with no header row at all, and `publish_gsheets` maps a parameter to a
    column by matching row 1 - so the next submission would have nowhere to go
    and would be dropped behind an HTTP 200. Deleting rows never touches row 1.
    """
    guard(tab)
    rows = len(values(sheet_id, token, tab))
    if rows <= 1:
        return 0

    grid = properties.get("gridProperties", {}).get("rowCount", rows)
    sheets._request(
        "POST",
        f"{sheets.SHEETS_API}/{sheet_id}:batchUpdate",
        token,
        json={
            "requests": [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": properties["sheetId"],
                            "dimension": "ROWS",
                            "startIndex": 1,
                            "endIndex": max(grid, rows),
                        }
                    }
                }
            ]
        },
    )
    return rows - 1


def snapshot(sheet_id: str, token: str, source: str, target: str) -> int:
    """Copy a tab's contents into another, creating it if needed."""
    guard(source)
    guard(target)
    rows = values(sheet_id, token, source)
    if len(rows) <= 1:
        return 0

    existing = tabs(sheet_id, token)
    if target not in existing:
        sheets._request(
            "POST",
            f"{sheets.SHEETS_API}/{sheet_id}:batchUpdate",
            token,
            json={"requests": [{"addSheet": {"properties": {"title": target}}}]},
        )

    sheets._request(
        "POST",
        f"{sheets.SHEETS_API}/{sheet_id}/values/{sheets.quote_tab(target)}:clear",
        token,
    )
    sheets._request(
        "PUT",
        f"{sheets.SHEETS_API}/{sheet_id}/values/{sheets.quote_tab(target)}!A1",
        token,
        params={"valueInputOption": "RAW"},
        json={"values": rows},
    )
    return len(rows) - 1


def report(
    sheet_id: str, token: str, sample: str, keep: tuple[str, ...]
) -> dict[str, dict]:
    """Print what is on each surface right now, and return the tab properties."""
    present = tabs(sheet_id, token)
    print("Workbook tabs:")
    for title, properties in present.items():
        hidden = " [hidden]" if properties.get("hidden") else ""
        if title not in TOUCHABLE:
            # Deliberately not counted. Counting means fetching the tab, and a
            # sign-up form in this workbook holds names and numbers that this
            # command has no reason to pull into memory to print a number it
            # will not act on.
            print(f"  (never touched) {title!r}{hidden}")
            continue
        rows = max(len(values(sheet_id, token, title)) - 1, 0)
        print(f"  {title!r}{hidden}: {rows} data row(s)")

    print("\nThis round's working files (--round):")
    for name in round_files(sample):
        path = Path(name)
        if path.is_file():
            print(f"  {name}: {path.stat().st_size} bytes")
        else:
            print(f"  {name}: absent")

    print("\nOther data-shaped files here (name them to --local to delete):")
    protected = {Path(n).resolve() for n in keep if Path(n).name}
    current = {Path(n).resolve() for n in round_files(sample)}
    found = sorted(
        {
            p
            for pattern in DATA_SHAPED
            for p in Path().glob(pattern)
            if p.is_file() and p.resolve() not in protected | current
        }
    )
    for path in found:
        print(f"  {path}: {path.stat().st_size} bytes")
    if not found:
        print("  (none)")

    print(f"\n  never deleted: {', '.join(keep)}")
    return present


def delete_files(
    names: tuple[str, ...], *, commit: bool, keep: tuple[str, ...] = ()
) -> int:
    """Delete the named files, never one named in ``keep``. Returns how many went.

    ``keep`` used to be printed as a guarantee and enforced nowhere, which made
    the report actively misleading: `--sample x.xlsx --signups x.xlsx --round
    --yes` printed "never deleted: x.xlsx" and then deleted it in the next
    breath. A sign-up export is hand-maintained and matched by the repository's
    blanket `*.xlsx` ignore, so that is unrecoverable respondent data.

    Comparison is by resolved path, not by string, so `./signups.xlsx` and
    `signups.xlsx` are recognised as the same file.
    """
    protected = set()
    for name in keep:
        try:
            protected.add(Path(name).resolve())
        except OSError:
            continue

    removed = 0
    for name in names:
        path = Path(name)
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            resolved = None
        if resolved is not None and resolved in protected:
            print(f"  KEPT {name} - named by --signups or reference material")
            continue
        if commit:
            path.unlink()
        print(f"  {'deleted' if commit else 'would delete'} {name}")
        removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    """Report, then run whichever operations were asked for."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Copy data and tracking into their _template tabs",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Delete every row below the header in data and tracking",
    )
    parser.add_argument(
        "--local",
        nargs="*",
        metavar="FILE",
        default=None,
        help=(
            "Delete exactly these files. Names are required: there is no default "
            "list, because a default here is somebody else's round. Run with no "
            "operations to see what is present."
        ),
    )
    parser.add_argument(
        "--round",
        action="store_true",
        help="Delete the finished round's sample, tracker and delivery log",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Actually do it. Without this, a dry run"
    )
    # The round is named by its sample, and --round deletes that sample, its
    # tracker and the shared delivery log. Required rather than defaulted: a
    # default here is a filename from somebody else's round, and the failure
    # mode is deleting the wrong one silently or the right one not at all.
    parser.add_argument(
        "--sample",
        required=True,
        help="This round's launch sample, e.g. rst2026_sample.xlsx",
    )
    parser.add_argument(
        "--signups",
        help="The hand-maintained sign-up export, never deleted",
    )
    args = parser.parse_args(argv)
    keep = KEEP + ((args.signups,) if args.signups else ())

    # credentials_from_env loads `.env` itself, and names whichever of the three
    # values is missing rather than failing on the first use of it.
    email, key, sheet_id = sheets.credentials_from_env()
    token = sheets.access_token(email, key)

    wanted = args.snapshot or args.truncate or bool(args.local) or args.round
    try:
        present = report(sheet_id, token, args.sample, keep)
    except sheets.SheetsError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    if not wanted:
        print(
            "\nNothing asked for. Pass --snapshot, --truncate, --local and/or --round."
        )
        return 0

    print(f"\n{'Doing' if args.yes else 'Would do'} the following:")

    try:
        # Snapshot first: the whole point of --snapshot --truncate in one
        # invocation is that the copy is taken before the original goes.
        if args.snapshot:
            for source, target in SNAPSHOTS.items():
                if source not in present:
                    print(f"  {source!r} is not in this workbook, skipping")
                    continue
                count = (
                    snapshot(sheet_id, token, source, target)
                    if args.yes
                    else max(len(values(sheet_id, token, source)) - 1, 0)
                )
                verb = "copied" if args.yes else "would copy"
                if count:
                    print(f"  {verb} {count} row(s) from {source!r} to {target!r}")
                else:
                    print(f"  {source!r} has no data rows, nothing to snapshot")

        if args.truncate:
            for tab in SNAPSHOTS:
                if tab not in present:
                    print(f"  {tab!r} is not in this workbook, skipping")
                    continue
                count = (
                    truncate(sheet_id, token, tab, present[tab])
                    if args.yes
                    else max(len(values(sheet_id, token, tab)) - 1, 0)
                )
                verb = "deleted" if args.yes else "would delete"
                print(f"  {verb} {count} row(s) from {tab!r}, header kept")

        if args.local:
            delete_files(tuple(args.local), commit=args.yes, keep=keep)

        if args.round:
            delete_files(round_files(args.sample), commit=args.yes, keep=keep)

    except (sheets.SheetsError, ResetError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    if not args.yes:
        print("\nDry run. Re-run with --yes to do it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
