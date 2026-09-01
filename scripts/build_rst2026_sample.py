"""Turn the RST 2026 sign-up export into a launch-ready sample file.

This is the only place a real number becomes something `rtt launch` will send
to. `rtt launch` validates that `Number` and `caseid` are present, that `caseid`
is neither blank nor duplicated, and that every requested column exists. It does
not validate the *value* in `Number` - no country check, no `whatsapp:` prefix
check. Whatever is in that cell goes to Twilio, and a malformed one fails
per-row at send time. On the day that is 13:55 with a room waiting, so the
checking happens here instead.

What this does:

  * resolves each number to ``whatsapp:+<E164>`` using the country the sign-up
    form collected, and refuses to guess when it cannot
  * drops anybody who did not tick the consent box
  * assigns a stable, non-identifying caseid
  * assigns arms 1 and 2 in balanced order, and **keeps every assignment it has
    already made**

Two properties are what make this safe to run repeatedly as sign-ups arrive,
which is how a training-day round actually works:

**Country comes from the form, per row.** Resolution is not one rule applied to
every number: the form asks for a country code, and this reads it. A bare
10-digit string is an Indian mobile, a US line and a Colombian mobile all at
once, and the only thing that can tell them apart is the respondent saying
which. A row that does not say, and whose number carries no ``+``, is reported
for a human rather than resolved - sending on the wrong resolution means
messaging a stranger.

**caseid and arm never move.** A rebuild reads the sample it is about to
overwrite and carries every number's existing caseid and arm forward unchanged;
only new sign-ups get new ones. `rtt launch --resume` keys on caseid, so an id
that shifted when the row count changed would re-send to somebody already
contacted, and an arm that shifted would move a respondent between treatments
after they had answered.

Nothing in the printed report contains a phone number. Numbers are Confidential
under IPA's data classification, and a report is the kind of thing that gets
pasted into a chat window.

Run with:

    just intake scripts/build_rst2026_sample.py
    uv run python scripts/build_rst2026_sample.py
    uv run python scripts/build_rst2026_sample.py signups.xlsx --prefix RST2026-TEST
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberType

#: Where the sign-up export is expected, so the everyday case takes no argument.
#: Gitignored by the repository's blanket `*.xlsx` rule, like every other file
#: that holds respondent data.
DEFAULT_INPUT = Path("rst2026_signups.xlsx")
DEFAULT_OUTPUT = Path("rst2026_sample.xlsx")

#: The columns `rtt launch` reads. A file that already has these is a launch
#: sample rather than a sign-up export, and is validated instead of converted.
LAUNCH_COLUMNS = ["Number", "caseid", "name", "arm"]

#: Header fragments, most specific first: `pick_column` takes the first hit.
NAME_HINTS = ("first name", "nombre", "name")
PHONE_HINTS = (
    "whatsapp number",
    "whatsapp",
    "phone",
    "mobile",
    "cell",
    "celular",
    "tel",
    "number",
)
COUNTRY_HINTS = ("country code", "country", "codigo de pais", "pais")
CONSENT_HINTS = ("i agree", "agree to receive", "consent", "consiento", "acepto")
TIMESTAMP_HINTS = ("timestamp", "marca temporal", "fecha")

#: Headers a role must never match, whatever the hints say. Ordering the hints
#: carefully is not enough on its own, because it only holds while the columns
#: stay in their current order - and both of these are one form edit away from
#: being wrong in a way nothing downstream would catch:
#:
#:   * "Organization Name" contains "name". Read as the respondent name, every
#:     opener goes out addressed to an employer.
#:   * "I agree to receive one WhatsApp message from IPA..." contains "whatsapp".
#:     Read as the phone column, every row fails to resolve at once - loudly,
#:     but at 13:55 with a room waiting.
NAME_AVOID = (
    "organisation",
    "organization",
    "company",
    "employer",
    "institution",
    "empresa",
)
PHONE_AVOID = ("agree", "consent", "acepto", "message from")

#: A ticked Google Forms checkbox exports its option text; an unticked one
#: exports nothing. These are the values that mean "not ticked" once something
#: has been typed into the cell by hand or by another export tool.
REFUSALS = frozenset({"", "no", "false", "0", "n", "nan", "none", "unchecked"})

#: WhatsApp runs on a mobile number, so a landline is not a slow path to a
#: respondent - it is a message that can never arrive, and there is no override
#: worth offering for that.
#:
#: FIXED_LINE_OR_MOBILE has to be accepted all the same. It does not mean "might
#: be a landline": it is what the library answers when a numbering plan does not
#: separate the two at all, which is the case for every number in +1. Refusing
#: it would refuse every US attendee.
REACHABLE = (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE)

#: The widest a country calling code gets.
MAX_COUNTRY_CODE_DIGITS = 3

#: Country names accepted in the country-code cell. The question asks for a
#: code and is free text rather than a dropdown, so a name is a normal thing to
#: receive and this list is load-bearing rather than a nicety.
#:
#: It is still not exhaustive, and cannot be: there is no reverse name lookup in
#: libphonenumber, so this is hand-kept while the *dial code* path covers every
#: country on earth through the library. A name that is not here is reported with
#: the remedy in the message rather than guessed at. Add to it freely.
#:
#: Keys are compared lowercased, unpunctuated and unaccented, so "Cote d Ivoire",
#: "Cote-d-Ivoire" and the properly accented spelling all arrive as one key.
COUNTRY_NAMES = {
    "india": "IN",
    "indonesia": "ID",
    "unitedstates": "US",
    "unitedstatesofamerica": "US",
    "usa": "US",
    "us": "US",
    "america": "US",
    "colombia": "CO",
    "philippines": "PH",
    "thephilippines": "PH",
    "vietnam": "VN",
    "vietnamsocialistrepublic": "VN",
    "thailand": "TH",
    "malaysia": "MY",
    "singapore": "SG",
    "cambodia": "KH",
    "myanmar": "MM",
    "burma": "MM",
    "laos": "LA",
    "laopdr": "LA",
    "brunei": "BN",
    "timorleste": "TL",
    "easttimor": "TL",
    "bangladesh": "BD",
    "pakistan": "PK",
    "srilanka": "LK",
    "nepal": "NP",
    "unitedkingdom": "GB",
    "uk": "GB",
    "kenya": "KE",
    "nigeria": "NG",
    "ghana": "GH",
    "uganda": "UG",
    "tanzania": "TZ",
    "rwanda": "RW",
    "zambia": "ZM",
    "ethiopia": "ET",
    "southafrica": "ZA",
    "egypt": "EG",
    "mexico": "MX",
    "peru": "PE",
    "brazil": "BR",
    "chile": "CL",
    "ecuador": "EC",
    "guatemala": "GT",
    "argentina": "AR",
    "bolivia": "BO",
    "canada": "CA",
    "australia": "AU",
    "japan": "JP",
    "china": "CN",
    "france": "FR",
    "germany": "DE",
    "spain": "ES",
    "netherlands": "NL",
    "italy": "IT",
    "switzerland": "CH",
    # West and Central Africa
    "senegal": "SN",
    "cotedivoire": "CI",
    "ivorycoast": "CI",
    "mali": "ML",
    "burkinafaso": "BF",
    "niger": "NE",
    "benin": "BJ",
    "togo": "TG",
    "guinea": "GN",
    "sierraleone": "SL",
    "liberia": "LR",
    "gambia": "GM",
    "thegambia": "GM",
    "cameroon": "CM",
    "chad": "TD",
    "drc": "CD",
    "democraticrepublicofthecongo": "CD",
    "congo": "CG",
    # East and Southern Africa
    "malawi": "MW",
    "mozambique": "MZ",
    "zimbabwe": "ZW",
    "botswana": "BW",
    "namibia": "NA",
    "lesotho": "LS",
    "eswatini": "SZ",
    "madagascar": "MG",
    "burundi": "BI",
    "somalia": "SO",
    "southsudan": "SS",
    "sudan": "SD",
    "eritrea": "ER",
    # North Africa and the Middle East
    "morocco": "MA",
    "tunisia": "TN",
    "algeria": "DZ",
    "jordan": "JO",
    "lebanon": "LB",
    "iraq": "IQ",
    "yemen": "YE",
    "turkey": "TR",
    "afghanistan": "AF",
    # Asia and the Pacific
    "bhutan": "BT",
    "maldives": "MV",
    "mongolia": "MN",
    "papuanewguinea": "PG",
    "fiji": "FJ",
    "timor": "TL",
    "southkorea": "KR",
    "korea": "KR",
    "taiwan": "TW",
    "hongkong": "HK",
    "newzealand": "NZ",
    "uzbekistan": "UZ",
    "kyrgyzstan": "KG",
    "tajikistan": "TJ",
    # Latin America and the Caribbean
    "honduras": "HN",
    "nicaragua": "NI",
    "elsalvador": "SV",
    "costarica": "CR",
    "panama": "PA",
    "dominicanrepublic": "DO",
    "haiti": "HT",
    "jamaica": "JM",
    "paraguay": "PY",
    "uruguay": "UY",
    "venezuela": "VE",
    # Europe
    "ireland": "IE",
    "portugal": "PT",
    "belgium": "BE",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "poland": "PL",
}


class Unresolved(ValueError):
    """A row that cannot become a send without a human deciding something."""


def pick_column(
    frame: pd.DataFrame,
    hints: tuple[str, ...],
    role: str,
    *,
    required: bool = True,
    avoid: tuple[str, ...] = (),
) -> str | None:
    """Find the export column for a role, preferring the most specific hint."""
    lowered = {
        str(c).strip().lower(): c
        for c in frame.columns
        if not any(term in str(c).strip().lower() for term in avoid)
    }
    for hint in hints:
        for lower, original in lowered.items():
            if hint in lower:
                return original
    if not required:
        return None
    raise SystemExit(
        f"Could not find a {role} column. Columns present: "
        f"{', '.join(map(str, frame.columns))}\n"
        f"Pass it explicitly with --{role}-col."
    )


def region_from(raw: object) -> str | None:
    """Turn a country-code cell into an ISO region code, or None.

    Tolerant of what a human puts in a box labelled "country code": ``+91``,
    ``91``, ``0091``, ``India``, ``IN``, and the ``India (+91)`` shape a Forms
    dropdown produces. Digits win when the cell has any, which is what makes
    the dropdown shape resolve on its code rather than on its spelling.
    """
    text = str(raw or "").strip()
    if not text or text.lower() in {"nan", "none"}:
        return None

    digits = re.sub(r"\D", "", text)
    if digits:
        digits = digits.lstrip("0") or digits
        if len(digits) > MAX_COUNTRY_CODE_DIGITS:
            # A phone number in the country column. Guessing which prefix of it
            # is the country code is how a message reaches a stranger.
            return None
        region = phonenumbers.region_code_for_country_code(int(digits))
        # The library answers "ZZ" for a calling code it does not know.
        return region if region and region != "ZZ" else None

    # Accents off before the lookup, so one key serves every spelling of a
    # name somebody types by hand.
    flattened = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(ch)
    )
    letters = re.sub(r"[^a-z]", "", flattened)
    if len(letters) == 2 and letters.upper() in phonenumbers.SUPPORTED_REGIONS:
        return letters.upper()
    return COUNTRY_NAMES.get(letters)


def normalise(raw: object, region: str | None) -> str:
    """Return ``whatsapp:+<E164>`` or raise :class:`Unresolved`.

    A number written with ``+`` or ``00`` is taken at its word and the row's
    country is ignored. Anything else is parsed against that country, which is
    what lets a trunk zero, a bare local number and a country code typed
    without a plus all resolve correctly - and what makes a row with no country
    a reported problem rather than a guess.
    """
    text = str(raw or "").strip()
    if not text or text.lower() in {"nan", "none"}:
        raise Unresolved("blank number")

    # `rtt launch` sends `whatsapp:+<E164>`, so this function has to accept its
    # own output: re-validating a sample it wrote must not refuse every row for
    # carrying the transport prefix it put there. The scheme is how the message
    # travels, not part of the number.
    for scheme in ("whatsapp:", "sms:", "tel:"):
        if text.lower().startswith(scheme):
            text = text[len(scheme) :].strip()
            break

    if text.startswith("00"):
        text = "+" + re.sub(r"\D", "", text)[2:]

    if not re.search(r"\d", text):
        raise Unresolved("no digits in the number")

    if text.startswith("+"):
        parse_region = None
    elif region:
        parse_region = region
    else:
        raise Unresolved(
            "no usable country on the row and the number has no '+', so which "
            "country it belongs to cannot be told from the digits - put the "
            "dial code (like +233) in the country column, or write the number "
            "in full with a '+'"
        )

    try:
        parsed = phonenumbers.parse(text, parse_region)
    except phonenumbers.NumberParseException as exc:
        raise Unresolved(f"not a phone number ({exc})") from exc

    if not phonenumbers.is_valid_number(parsed):
        where = f"+{parsed.country_code}" if parsed.country_code else "that country"
        raise Unresolved(f"not a valid number for {where}")

    kind = phonenumbers.number_type(parsed)
    if kind not in REACHABLE:
        label = "a landline" if kind == PhoneNumberType.FIXED_LINE else "not a mobile"
        raise Unresolved(
            f"{label}, and WhatsApp only reaches mobiles - ask them for a "
            f"mobile number rather than sending a message that cannot arrive"
        )

    return "whatsapp:" + phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def consented(raw: object) -> bool:
    """Return True when the consent cell says yes. An empty box is not a yes."""
    return str(raw or "").strip().lower() not in REFUSALS


def read_existing(path: Path) -> tuple[dict[str, tuple[str, str]], int]:
    """Read the assignments already made, keyed on the resolved number.

    Returns ``(number -> (caseid, arm), highest index seen)``. A missing or
    unreadable file is not an error: the first build has nothing to carry.
    """
    if not path.is_file():
        return {}, 0

    try:
        frame = pd.read_excel(path, dtype=str)
    except Exception:
        return {}, 0

    if not {"Number", "caseid"}.issubset(frame.columns):
        return {}, 0

    assignments: dict[str, tuple[str, str]] = {}
    highest = 0
    for record in frame.to_dict("records"):
        number = str(record.get("Number") or "").strip()
        caseid = str(record.get("caseid") or "").strip()
        if not number or not caseid:
            continue
        assignments[number] = (caseid, str(record.get("arm") or "").strip())
        tail = caseid.rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return assignments, highest


def minority_arm(counts: dict[str, int], rng: random.Random) -> str:
    """Return the arm with fewer members, coin-flipped on a tie.

    Filling the minority arm keeps a growing round balanced. Reshuffling the
    whole list would too, but only by moving people already contacted.
    """
    if counts["1"] < counts["2"]:
        return "1"
    if counts["2"] < counts["1"]:
        return "2"
    return rng.choice(["1", "2"])


def build(
    frame: pd.DataFrame, args: argparse.Namespace
) -> tuple[pd.DataFrame, list[tuple[int, str]], list[str]]:
    """Convert a sign-up export into a launch sample.

    Returns ``(sample, problems, notes)``: problems are rows a human has to
    look at, notes are things worth saying out loud that do not block a send.
    """
    name_col = args.name_col or pick_column(frame, NAME_HINTS, "name", avoid=NAME_AVOID)
    phone_col = args.phone_col or pick_column(
        frame, PHONE_HINTS, "phone", avoid=PHONE_AVOID
    )
    country_col = args.country_col or pick_column(
        frame, COUNTRY_HINTS, "country", required=False
    )
    consent_col = pick_column(frame, CONSENT_HINTS, "consent", required=False)
    stamp_col = pick_column(frame, TIMESTAMP_HINTS, "timestamp", required=False)
    # Not found by hint: a column literally called `Number` is the launch
    # column, and matching it loosely would catch "Your WhatsApp number" too.
    number_col = "Number" if "Number" in frame.columns else None

    print(f"  name    column: {name_col!r}")
    print(f"  phone   column: {phone_col!r}")
    print(f"  country column: {country_col!r}")
    print(f"  consent column: {consent_col!r}")
    print(f"  order   column: {stamp_col!r}")
    print(f"  Number  column: {number_col!r} (cross-checked, not trusted blindly)\n")

    notes: list[str] = []
    if country_col is None:
        notes.append(
            "No country column found, so only numbers written with a '+' can "
            "resolve. Pass --country-col if the export has one under another name."
        )
    if consent_col is None:
        notes.append(
            "No consent column found, so every row is treated as having "
            "consented. Check that against the form before sending."
        )

    # Form order is the stable order: it does not change when a row is fixed
    # upstream, which is what keeps index assignment reproducible. Sorting on a
    # parsed timestamp rather than on sheet position also survives an export
    # that came back ordered differently.
    ordered = frame.copy()
    ordered["_row"] = range(2, len(ordered) + 2)
    if stamp_col is not None:
        ordered["_when"] = pd.to_datetime(
            ordered[stamp_col], errors="coerce", format="mixed"
        )
        ordered = ordered.sort_values(["_when", "_row"], kind="stable")

    existing, highest = read_existing(args.out)
    rng = random.Random(args.seed)  # noqa: S311 - reproducibility, not secrecy

    rows: list[dict[str, str]] = []
    problems: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    refused = 0

    for record in ordered.to_dict("records"):
        position = int(record["_row"])

        if consent_col is not None and not consented(record.get(consent_col)):
            refused += 1
            continue

        region = region_from(record.get(country_col)) if country_col else None
        try:
            number = normalise(record.get(phone_col), region)
        except Unresolved as exc:
            # An explicit Number is allowed to rescue a row the country column
            # could not resolve: it carries its own country, which is more than
            # the two source cells managed.
            try:
                number = normalise(record.get(number_col), None)
            except Unresolved:
                problems.append((position, str(exc)))
                continue

        # A workbook that keeps its own `Number` column is trusted - but never
        # silently. If the two disagree, one of them is a typo and there is no
        # way to tell which from here, so the row is reported rather than sent
        # on a coin flip.
        supplied = record.get(number_col) if number_col else None
        if supplied and str(supplied).strip():
            try:
                explicit = normalise(supplied, None)
            except Unresolved as exc:
                problems.append((position, f"the Number column is {exc}"))
                continue
            if explicit != number:
                problems.append(
                    (
                        position,
                        "the Number column and the country + number columns "
                        "disagree - fix whichever is wrong, they cannot both "
                        "be right",
                    )
                )
                continue
            number = explicit

        if number in seen:
            # Two sign-ups from one person. Sending twice starts a second
            # execution, and the second overwrites the first one's answers.
            problems.append(
                (position, f"same number as row {seen[number]}, already in the sample")
            )
            continue
        seen[number] = position

        name = str(record.get(name_col) or "").strip().split(" ")[0] or "there"
        rows.append({"Number": number, "name": name})

    if refused:
        notes.append(f"{refused} sign-up(s) did not tick consent and were left out.")

    counts = {"1": 0, "2": 0}
    for _caseid, arm in existing.values():
        if arm in counts:
            counts[arm] += 1

    carried = 0
    index = highest
    for row in rows:
        prior = existing.get(row["Number"])
        if prior:
            row["caseid"], row["arm"] = prior
            carried += 1
            continue
        index += 1
        row["caseid"] = f"{args.prefix}-{index:03d}"
        row["arm"] = minority_arm(counts, rng)
        counts[row["arm"]] += 1

    dropped = set(existing) - set(seen)
    if dropped:
        notes.append(
            f"{len(dropped)} number(s) in the previous sample are not in this "
            f"export. Any that were already sent to stay in the tracker and "
            f"stop being followed - check that before treating this as a fix."
        )

    notes.append(f"{carried} assignment(s) carried forward, {len(rows) - carried} new.")
    return _as_sample(rows), problems, notes


def validate_launch_shape(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, list[tuple[int, str]], list[str]]:
    """Re-check a file that is already a launch sample, and pass it through.

    This command is the one place a number becomes a send, so it accepts a
    hand-made sample too rather than letting somebody find out at send time
    that theirs was never checked.
    """
    print("  input is already in launch shape - validating, not converting\n")
    rows: list[dict[str, str]] = []
    problems: list[tuple[int, str]] = []
    for position, record in enumerate(frame.to_dict("records"), start=2):
        try:
            number = normalise(record.get("Number"), None)
        except Unresolved as exc:
            problems.append((position, str(exc)))
            continue
        rows.append(
            {
                "Number": number,
                "caseid": str(record.get("caseid") or "").strip(),
                "name": str(record.get("name") or "there").strip(),
                "arm": str(record.get("arm") or "").strip(),
            }
        )

    blank = sum(1 for row in rows if not row["caseid"])
    notes = [f"{blank} row(s) have a blank caseid."] if blank else []
    return _as_sample(rows), problems, notes


def _as_sample(rows: list[dict[str, str]]) -> pd.DataFrame:
    """Rows as a launch sample, with the columns in the order launch reads."""
    if not rows:
        return pd.DataFrame(columns=LAUNCH_COLUMNS)
    return pd.DataFrame(rows)[LAUNCH_COLUMNS]


def read_any(path: Path) -> pd.DataFrame:
    """Read a .csv or .xlsx export as strings throughout."""
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str)


def write_review(path: Path, problems: list[tuple[int, str]]) -> None:
    """Write the rows a person has to look at, or clear a stale file.

    The terminal report is the same information and it is where a person
    actually reads it, so this is not a replacement for printing. It is for
    afterwards: the rows that did not resolve are the list somebody takes back
    to whoever owns the export, and a terminal that has been closed cannot be
    taken anywhere. A round that ran without this leaves no evidence of what it
    declined to send - which is the same silent-success failure this whole
    script exists to refuse.

    Deleting the file when there is nothing to review matters as much as writing
    it. A stale one from the previous run reads as current, and a list of
    problems that were fixed a week ago is worse than no list.

    Carries the export's row number rather than any cell from it. That is
    enough to find the row in the source, and it keeps the promise the module
    docstring makes: nothing this script writes outside the sample contains a
    phone number.
    """
    if not problems:
        path.unlink(missing_ok=True)
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("export_row", "reason"))
        writer.writerows(problems)


def main(argv: list[str] | None = None) -> int:
    """Read the sign-up export, write the sample, report what needs a human."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "signups",
        type=Path,
        nargs="?",
        default=DEFAULT_INPUT,
        help=f"Sign-up export, .csv or .xlsx (default: {DEFAULT_INPUT})",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--seed", type=int, default=20260826, help="Tie-break seed for arm balance"
    )
    parser.add_argument("--prefix", default="RST2026-LIVE", help="caseid prefix")
    parser.add_argument("--name-col", default=None)
    parser.add_argument("--phone-col", default=None)
    parser.add_argument("--country-col", default=None)
    parser.add_argument(
        "--review",
        type=Path,
        default=None,
        help="Where to write the rows needing review (default: <out>_needs_human_review.csv)",
    )
    args = parser.parse_args(argv)
    review_path = args.review or args.out.with_name(
        f"{args.out.stem}_needs_human_review.csv"
    )

    if not args.signups.is_file():
        print(
            f"Not found: {args.signups}\n\n"
            f"Put the sign-up export there, or name one:\n"
            f'  just intake {Path(__file__).as_posix()} "path/to/export.xlsx"',
            file=sys.stderr,
        )
        return 1

    try:
        frame = read_any(args.signups)
    except Exception as exc:
        print(f"Could not read {args.signups}: {exc}", file=sys.stderr)
        return 1

    print(f"Reading {len(frame)} sign-up(s) from {args.signups.name}")

    if {"Number", "caseid"}.issubset(frame.columns):
        sample, problems, notes = validate_launch_shape(frame)
    else:
        sample, problems, notes = build(frame, args)

    if sample.empty:
        # stderr is unbuffered and stdout is not, so without this the failure
        # prints above the column report it is about - which reads as though no
        # columns were found at all.
        sys.stdout.flush()
        print("No usable rows. Nothing written.", file=sys.stderr)
        for position, reason in problems:
            print(f"  row {position}: {reason}", file=sys.stderr)
        # Written on this path above all: no sample means the whole export needs
        # work, and that is the case where a list to hand back is worth most.
        write_review(review_path, problems)
        if problems:
            print(f"\nAlso written to {review_path}", file=sys.stderr)
        return 1

    sample.to_excel(args.out, index=False, sheet_name="sample")

    print(f"Wrote {args.out}  ({len(sample)} row(s))")
    print(f"  ARM 1: {(sample['arm'] == '1').sum()}")
    print(f"  ARM 2: {(sample['arm'] == '2').sum()}")
    print(f"  caseids: {sample['caseid'].iloc[0]} .. {sample['caseid'].iloc[-1]}")

    for note in notes:
        print(f"  note: {note}")

    write_review(review_path, problems)

    if problems:
        print(f"\n{len(problems)} row(s) need human review (export row -> reason):")
        for position, reason in problems:
            print(f"  row {position}: {reason}")
        print(f"\nAlso written to {review_path}")
        print(
            "Fix them in the export and re-run - none were sent anywhere, and "
            "a re-run does not move anybody already assigned."
        )
    else:
        print("\nEvery sign-up resolved.")

    stem = args.out.stem
    print(
        "\nNext:\n"
        f'  just send {stem} caseid,name,arm "--dry-run"   # checks, sends nothing\n'
        f"  just send {stem} caseid,name,arm   # sends to whoever is left"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
