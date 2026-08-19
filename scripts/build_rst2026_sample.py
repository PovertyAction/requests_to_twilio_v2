"""Turn the RST 2026 Google Form export into a launch-ready sample file.

`rtt launch` validates that `Number` and `caseid` are present, that `caseid` is
neither blank nor duplicated, and that every requested column exists. It does
not validate the *value* in `Number` - no country code check, no `whatsapp:`
prefix check. Whatever is in that cell goes to Twilio, and a malformed one fails
per-row at send time. On the day that is 13:55 with a room waiting, so the
checking happens here instead.

What this does:

  * normalises each number to ``whatsapp:+<E164>``
  * assigns a stable, non-identifying caseid
  * takes the first name only, which is what the approved opener's {{1}} carries
  * assigns arms 1 and 2 in balanced, seeded, random order

Arms are randomised here rather than in the flow on purpose: it happens offline,
where the balance can be checked before anybody is contacted, and the same seed
reproduces the same assignment if the file has to be rebuilt.

Nothing in the printed report contains a phone number. Numbers are Confidential
under IPA's data classification, and a report is the kind of thing that gets
pasted into a chat window.

Run with:

    uv run python scripts/build_rst2026_sample.py <form-export.csv>
    uv run python scripts/build_rst2026_sample.py <form-export.xlsx> --out rst2026_sample.xlsx
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

import pandas as pd

#: India, because the training is in Jaipur and most attendees will type a bare
#: local number. Anybody else has to write their own country code with a `+` -
#: guessing a country from a bare 10-digit string is how you silently message a
#: stranger.
DEFAULT_REGION = "91"

#: Digits in a local mobile number in the default region. India is 10.
#:
#: This has to be length-based rather than prefix-based, and the reason is a
#: trap: Indian mobile numbers start with 6-9, so a perfectly ordinary local
#: number like 9123456789 *begins with the country code 91*. Deciding "it
#: already carries its country code" by looking at the prefix turns that into
#: +9123456789 - ten digits, country code missing - and Twilio fails the row at
#: send time. Length tells the two apart; the prefix cannot.
LOCAL_LENGTH = 10

#: E.164 allows 15 digits maximum; below about 8 it is not a mobile number.
MIN_DIGITS = 8
MAX_DIGITS = 15

NAME_HINTS = ("first name", "nombre", "name", "nombre completo")
PHONE_HINTS = ("whatsapp", "phone", "number", "mobile", "cell", "tel", "celular")


class Unresolved(ValueError):
    """A number that cannot be turned into E.164 without a human deciding."""


def pick_column(frame: pd.DataFrame, hints: tuple[str, ...], role: str) -> str:
    """Find the Form column for a role, preferring the most specific hint."""
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    for hint in hints:
        for lower, original in lowered.items():
            if hint in lower:
                return original
    raise SystemExit(
        f"Could not find a {role} column. Columns present: "
        f"{', '.join(map(str, frame.columns))}\n"
        f"Pass it explicitly with --{role}-col."
    )


def normalise(raw: str, region: str, local_length: int = LOCAL_LENGTH) -> str:
    """Return ``whatsapp:+<E164>`` or raise Unresolved.

    Deliberately refuses to guess. A number written with `+` or `00` is taken at
    its word. A bare number is resolved by *length* against the default region,
    and anything that is neither a local-length nor a region-plus-local-length
    string is reported for a human instead.

    That last part is the point. A US number typed as "15550000004" and an
    unfamiliar national format are indistinguishable once punctuation is
    stripped, so the ambiguous case gets a name in the report rather than a rule
    that is right most of the time and silently wrong the rest.
    """
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none"}:
        raise Unresolved("blank")

    explicit = text.startswith("+") or text.startswith("00")
    digits = re.sub(r"\D", "", text)

    if text.startswith("00"):
        digits = digits[2:]

    if not digits:
        raise Unresolved("no digits")

    if not explicit:
        # A leading trunk zero is domestic notation and is dropped before the
        # country code goes on.
        digits = digits.lstrip("0")
        if len(digits) == local_length:
            digits = region + digits
        elif not (
            digits.startswith(region) and len(digits) == len(region) + local_length
        ):
            raise Unresolved(
                f"{len(digits)} digits and no '+' - cannot tell which country. "
                f"Expected {local_length} for +{region}, or write it with a '+'"
            )

    if not (MIN_DIGITS <= len(digits) <= MAX_DIGITS):
        raise Unresolved(f"{len(digits)} digits, expected {MIN_DIGITS}-{MAX_DIGITS}")

    return f"whatsapp:+{digits}"


def balanced_arms(count: int, seed: int) -> list[str]:
    """Half in each arm, shuffled. An odd row goes to ARM 2.

    ARM 2 takes the extra because ARM 1 is the counter-example: one respondent
    more or less on the arm that produces uncodeable text changes nothing, and
    ARM 2 is the arm whose answers get charted live.
    """
    arms = ["1"] * (count // 2) + ["2"] * (count - count // 2)
    # noqa S311: a seeded, reproducible PRNG is the requirement here, not a
    # cryptographic one. Rebuilding the sample from the same export has to
    # produce the same assignment, or a re-run silently moves people between
    # arms after some of them have already answered.
    random.Random(seed).shuffle(arms)  # noqa: S311
    return arms


def main(argv: list[str] | None = None) -> int:
    """Read the Form export, write the sample, and report what needs a human."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "form_export", type=Path, help="Google Form export, .csv or .xlsx"
    )
    parser.add_argument("--out", type=Path, default=Path("rst2026_sample.xlsx"))
    parser.add_argument("--region", default=DEFAULT_REGION, help="Default country code")
    parser.add_argument(
        "--local-length",
        type=int,
        default=LOCAL_LENGTH,
        help="Digits in a local mobile number in the default region",
    )
    parser.add_argument(
        "--seed", type=int, default=20260826, help="Arm randomisation seed"
    )
    parser.add_argument("--prefix", default="RST2026", help="caseid prefix")
    parser.add_argument("--name-col", default=None)
    parser.add_argument("--phone-col", default=None)
    args = parser.parse_args(argv)

    if not args.form_export.is_file():
        print(f"Not found: {args.form_export}", file=sys.stderr)
        return 1

    if args.form_export.suffix.lower() in {".xlsx", ".xlsm"}:
        frame = pd.read_excel(args.form_export, dtype=str)
    else:
        frame = pd.read_csv(args.form_export, dtype=str)

    name_col = args.name_col or pick_column(frame, NAME_HINTS, "name")
    phone_col = args.phone_col or pick_column(frame, PHONE_HINTS, "phone")
    print(f"Reading {len(frame)} response(s) from {args.form_export.name}")
    print(f"  name  column: {name_col!r}")
    print(f"  phone column: {phone_col!r}\n")

    rows: list[dict[str, str]] = []
    problems: list[tuple[int, str]] = []
    seen: set[str] = set()

    for position, record in enumerate(frame.to_dict("records"), start=2):
        try:
            number = normalise(
                record.get(phone_col, ""), args.region, args.local_length
            )
        except Unresolved as exc:
            problems.append((position, str(exc)))
            continue

        if number in seen:
            # Two Form submissions from one person. Sending twice would start a
            # second execution and overwrite the first one's answers.
            problems.append((position, "duplicate number, already in the sample"))
            continue
        seen.add(number)

        name = str(record.get(name_col, "") or "").strip().split(" ")[0] or "there"
        rows.append({"Number": number, "name": name})

    if not rows:
        print("No usable rows. Nothing written.", file=sys.stderr)
        return 1

    arms = balanced_arms(len(rows), args.seed)
    for index, (row, arm) in enumerate(zip(rows, arms), start=1):
        row["caseid"] = f"{args.prefix}-{index:03d}"
        row["arm"] = arm

    out = pd.DataFrame(rows)[["Number", "caseid", "name", "arm"]]
    out.to_excel(args.out, index=False, sheet_name="sample")

    print(f"Wrote {args.out}  ({len(out)} row(s))")
    print(f"  ARM 1: {(out['arm'] == '1').sum()}")
    print(f"  ARM 2: {(out['arm'] == '2').sum()}")
    print(f"  caseids: {out['caseid'].iloc[0]} .. {out['caseid'].iloc[-1]}")

    if problems:
        print(f"\n{len(problems)} row(s) need a human (spreadsheet row -> reason):")
        for position, reason in problems:
            print(f"  row {position}: {reason}")
        print("\nFix them in the Form export and re-run; none were sent anywhere.")
    else:
        print("\nEvery response resolved.")

    print(
        f"\nNext:\n"
        f'  just launch "{args.out} --columns caseid,name,arm --dry-run"\n'
        f'  just launch "{args.out} --columns caseid,name,arm"'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
