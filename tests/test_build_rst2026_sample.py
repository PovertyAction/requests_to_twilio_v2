"""Tests for the sign-up-to-sample builder.

The builder is the only thing between a sign-up form and a message going out,
so these tests are about the two ways it can hurt somebody: resolving a number
to the wrong country, and moving a caseid or an arm that has already been used.

Everything here runs in-process against a dataframe. Nothing contacts Twilio,
and no number in this file is real - they only have to be structurally valid
for the country under test, which is exactly what the builder checks.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_rst2026_sample as builder  # noqa: E402

#: The Google Form's own header row, verbatim. The builder finds its columns by
#: hint rather than by position, and the hints have to survive these exact
#: names - "Organization Name" is why the name hints put "first name" first.
FORM_COLUMNS = [
    "Timestamp",
    "First Name",
    "Country code",
    "Your WhatsApp number",
    "Organization Name",
    "I agree to receive one WhatsApp message from IPA for this session.",
]

CONSENT_TEXT = "I agree"


def make_args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    """Return the namespace `main` would have built, pointed at a temp directory."""
    values: dict[str, object] = {
        "signups": tmp_path / "signups.xlsx",
        "out": tmp_path / "sample.xlsx",
        "seed": 20260826,
        "prefix": "RST2026-LIVE",
        "name_col": None,
        "phone_col": None,
        "country_col": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def signup(
    when: str,
    name: str,
    country: str,
    number: str,
    consent: str = CONSENT_TEXT,
) -> list[str]:
    """One row shaped like the form export."""
    return [when, name, country, number, "IPA", consent]


def form(*rows: list[str]) -> pd.DataFrame:
    """Return a frame shaped like the form export."""
    return pd.DataFrame(list(rows), columns=FORM_COLUMNS)


@pytest.mark.parametrize(
    ("country", "number", "expected"),
    [
        # India. The bare local number begins with its own country code, which
        # is the trap a prefix-based rule falls into.
        ("91", "9123456789", "whatsapp:+919123456789"),
        ("+91", "9123456789", "whatsapp:+919123456789"),
        ("0091", "9123456789", "whatsapp:+919123456789"),
        ("India", "9123456789", "whatsapp:+919123456789"),
        ("IN", "9123456789", "whatsapp:+919123456789"),
        ("India (+91)", "9123456789", "whatsapp:+919123456789"),
        ("91", "919123456789", "whatsapp:+919123456789"),
        # Indonesia, where the trunk zero is how everybody writes it.
        ("62", "08123456789", "whatsapp:+628123456789"),
        ("Indonesia", "8123456789", "whatsapp:+628123456789"),
        # The US, whose numbers come back FIXED_LINE_OR_MOBILE.
        ("1", "4155551234", "whatsapp:+14155551234"),
        ("USA", "(415) 555-1234", "whatsapp:+14155551234"),
        ("1", "14155551234", "whatsapp:+14155551234"),
        # Colombia.
        ("57", "3001234567", "whatsapp:+573001234567"),
        ("Colombia", "300 123 4567", "whatsapp:+573001234567"),
        # The south-east Asian tail.
        ("63", "09171234567", "whatsapp:+639171234567"),
        ("84", "0912345678", "whatsapp:+84912345678"),
        ("65", "81234567", "whatsapp:+6581234567"),
        ("66", "0812345678", "whatsapp:+66812345678"),
        ("60", "0123456789", "whatsapp:+60123456789"),
        ("855", "012345678", "whatsapp:+85512345678"),
    ],
)
def test_resolves_every_expected_country(
    tmp_path: Path, country: str, number: str, expected: str
) -> None:
    """Each country the training draws from resolves to E.164."""
    sample, problems, _ = builder.build(
        form(signup("2026-08-24 09:00:00", "Priya", country, number)),
        make_args(tmp_path),
    )
    assert problems == []
    assert sample["Number"].tolist() == [expected]


def test_a_bare_number_with_no_country_is_refused(tmp_path: Path) -> None:
    """Ten digits and no country is India, the US or Colombia - so it is asked.

    This is the case that would otherwise message a stranger, and it is why
    resolution reads the form rather than applying a default region.
    """
    sample, problems, _ = builder.build(
        form(signup("2026-08-24 09:00:00", "Sam", "", "9123456789")),
        make_args(tmp_path),
    )
    assert sample.empty
    assert len(problems) == 1
    # The refusal has to say what to write instead, or it is a dead end for
    # whoever is fixing the export with a room waiting.
    assert "no usable country" in problems[0][1]
    assert "dial code" in problems[0][1]


def test_a_plus_number_needs_no_country(tmp_path: Path) -> None:
    """A number that says which country it is in is taken at its word."""
    sample, problems, _ = builder.build(
        form(signup("2026-08-24 09:00:00", "Sam", "", "+62 812 345 6789")),
        make_args(tmp_path),
    )
    assert problems == []
    assert sample["Number"].tolist() == ["whatsapp:+628123456789"]


def test_a_landline_is_refused(tmp_path: Path) -> None:
    """WhatsApp reaches mobiles, so a landline is a message that never arrives."""
    sample, problems, _ = builder.build(
        form(signup("2026-08-24 09:00:00", "Sam", "62", "02112345678")),
        make_args(tmp_path),
    )
    assert sample.empty
    assert "landline" in problems[0][1]


def test_an_invalid_number_is_refused(tmp_path: Path) -> None:
    """Too few digits for the country given is reported, not sent."""
    _, problems, _ = builder.build(
        form(signup("2026-08-24 09:00:00", "Sam", "91", "12345")),
        make_args(tmp_path),
    )
    assert len(problems) == 1


def test_unticked_consent_is_left_out(tmp_path: Path) -> None:
    """An empty consent box is not a yes, and is not a problem row either."""
    sample, problems, notes = builder.build(
        form(
            signup("2026-08-24 09:00:00", "Priya", "91", "9123456789"),
            signup("2026-08-24 09:05:00", "Sam", "91", "9123456780", consent=""),
        ),
        make_args(tmp_path),
    )
    assert len(sample) == 1
    assert problems == []
    assert any("did not tick consent" in note for note in notes)


def test_the_same_number_twice_is_sent_to_once(tmp_path: Path) -> None:
    """A second sign-up would start a second execution over the first."""
    sample, problems, _ = builder.build(
        form(
            signup("2026-08-24 09:00:00", "Priya", "91", "9123456789"),
            signup("2026-08-24 09:30:00", "Priya", "+91", "09123456789"),
        ),
        make_args(tmp_path),
    )
    assert len(sample) == 1
    assert "same number as row 2" in problems[0][1]


def test_a_rebuild_moves_nobody(tmp_path: Path) -> None:
    """The property the whole round rests on.

    `rtt launch --resume` keys on caseid, so an id that shifted when three more
    people signed up would re-send to somebody already contacted - and an arm
    that shifted would move a respondent between treatments after they had
    answered.
    """
    args = make_args(tmp_path)
    first = form(
        signup("2026-08-24 09:00:00", "Priya", "91", "9123456789"),
        signup("2026-08-24 09:05:00", "Sam", "62", "08123456789"),
    )
    sample, _, _ = builder.build(first, args)
    sample.to_excel(args.out, index=False, sheet_name="sample")
    before = dict(zip(sample["Number"], zip(sample["caseid"], sample["arm"])))

    later = form(
        *[first.iloc[i].tolist() for i in range(len(first))],
        signup("2026-08-24 10:00:00", "Ana", "57", "3001234567"),
        signup("2026-08-24 10:05:00", "Lee", "65", "81234567"),
        signup("2026-08-24 10:10:00", "Mai", "84", "0912345678"),
    )
    rebuilt, problems, notes = builder.build(later, args)

    assert problems == []
    assert len(rebuilt) == 5
    after = dict(zip(rebuilt["Number"], zip(rebuilt["caseid"], rebuilt["arm"])))
    for number, assignment in before.items():
        assert after[number] == assignment
    assert any("2 assignment(s) carried forward, 3 new" in note for note in notes)
    assert sorted(rebuilt["caseid"]) == [f"RST2026-LIVE-{i:03d}" for i in range(1, 6)]


def test_arms_stay_balanced_as_signups_arrive(tmp_path: Path) -> None:
    """Filling the minority arm keeps balance without reshuffling anybody."""
    args = make_args(tmp_path)
    numbers = [
        ("91", "9123456789"),
        ("62", "08123456789"),
        ("57", "3001234567"),
        ("65", "81234567"),
        ("84", "0912345678"),
        ("66", "0812345678"),
    ]
    rows = [
        signup(f"2026-08-24 09:0{i}:00", f"P{i}", country, number)
        for i, (country, number) in enumerate(numbers)
    ]

    # One sign-up at a time, rewriting the sample each round, which is what
    # happens on the day.
    for count in range(1, len(rows) + 1):
        sample, _, _ = builder.build(form(*rows[:count]), args)
        sample.to_excel(args.out, index=False, sheet_name="sample")

    counts = sample["arm"].value_counts().to_dict()
    assert counts.get("1") == 3
    assert counts.get("2") == 3


def test_a_dropped_signup_is_called_out(tmp_path: Path) -> None:
    """Someone in the old sample and not the new one may already be mid-survey."""
    args = make_args(tmp_path)
    first = form(
        signup("2026-08-24 09:00:00", "Priya", "91", "9123456789"),
        signup("2026-08-24 09:05:00", "Sam", "62", "08123456789"),
    )
    sample, _, _ = builder.build(first, args)
    sample.to_excel(args.out, index=False, sheet_name="sample")

    _, _, notes = builder.build(form(first.iloc[0].tolist()), args)
    assert any("not in this export" in note for note in notes)


def test_the_report_never_carries_a_number(tmp_path: Path) -> None:
    """A report gets pasted into chat windows; numbers are Confidential."""
    digits = "9123456789"
    _, problems, notes = builder.build(
        form(
            signup("2026-08-24 09:00:00", "Sam", "", digits),
            signup("2026-08-24 09:05:00", "Ana", "62", "02112345678"),
        ),
        make_args(tmp_path),
    )
    printed = " ".join(reason for _, reason in problems) + " ".join(notes)
    assert digits not in printed
    assert "02112345678" not in printed


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("+91", "IN"),
        ("91", "IN"),
        ("0091", "IN"),
        ("India (+91)", "IN"),
        ("IN", "IN"),
        ("india", "IN"),
        ("62", "ID"),
        ("Indonesia", "ID"),
        ("1", "US"),
        ("USA", "US"),
        ("57", "CO"),
        ("", None),
        (None, None),
        ("nan", None),
        # A phone number pasted into the country box. Guessing which prefix of
        # it is the country code is how a message reaches a stranger.
        ("9123456789", None),
        ("Atlantis", None),
        ("999", None),
        # Names that a free-text country box actually receives. There is no
        # dropdown constraining this cell, so the name path is load-bearing.
        ("Ghana", "GH"),
        ("233", "GH"),
        ("Senegal", "SN"),
        ("Sierra Leone", "SL"),
        ("Papua New Guinea", "PG"),
        ("Morocco", "MA"),
        # Accents off before the lookup, so one key serves every spelling.
        ("Cote d Ivoire", "CI"),
        ("Cote-d-Ivoire", "CI"),
        ("C\u00f4te d\u2019Ivoire", "CI"),
        # A dial code needs no table at all - the library knows every country.
        ("221", "SN"),
        ("675", "PG"),
        ("998", "UZ"),
    ],
)
def test_region_from(cell: object, expected: str | None) -> None:
    """The country cell is read tolerantly, and refuses rather than guesses."""
    assert builder.region_from(cell) == expected


def test_a_launch_sample_is_validated_not_converted() -> None:
    """A hand-made sample still gets its numbers checked."""
    frame = pd.DataFrame(
        [
            {
                "Number": "whatsapp:+919123456789",
                "caseid": "RST2026-TEST-001",
                "name": "Priya",
                "arm": "1",
            },
            {
                "Number": "+91 12345",
                "caseid": "RST2026-TEST-002",
                "name": "Sam",
                "arm": "2",
            },
        ]
    )
    sample, problems, _ = builder.validate_launch_shape(frame)
    assert sample["caseid"].tolist() == ["RST2026-TEST-001"]
    assert len(problems) == 1


def test_consented() -> None:
    """A ticked box is anything but the values that mean empty."""
    assert builder.consented("I agree")
    assert builder.consented("Yes")
    assert not builder.consented("")
    assert not builder.consented(None)
    assert not builder.consented("No")


#: The seven-column shape a maintained workbook has: the form's own columns plus
#: a `Number` column somebody keeps alongside them. Numbers here are synthetic -
#: `300 555 04xx` is a valid Colombian mobile that nobody holds.
HYBRID_COLUMNS = [*FORM_COLUMNS, "Number"]


def hybrid(
    number: str, *, country: str = "+57", local: str = "3005550420"
) -> pd.DataFrame:
    """One row of the seven-column workbook, with an explicit Number."""
    return pd.DataFrame(
        [
            [
                "2026-08-24 09:00:00",
                "Ana",
                country,
                local,
                "IPA",
                CONSENT_TEXT,
                number,
            ]
        ],
        columns=HYBRID_COLUMNS,
    )


def test_an_explicit_number_column_is_used(tmp_path: Path) -> None:
    """A workbook that keeps its own Number column is trusted."""
    sample, problems, _ = builder.build(
        hybrid("whatsapp:+573005550420"), make_args(tmp_path)
    )
    assert problems == []
    assert sample["Number"].tolist() == ["whatsapp:+573005550420"]


def test_a_disagreeing_number_column_is_reported(tmp_path: Path) -> None:
    """Trusted, but never silently.

    If the Number column and the country + number columns disagree, one of them
    is a typo and nothing here can tell which. Picking either would send a
    message on a coin flip, so the row is reported instead.
    """
    sample, problems, _ = builder.build(
        hybrid("whatsapp:+573005550421"), make_args(tmp_path)
    )
    assert sample.empty
    assert "disagree" in problems[0][1]


def test_an_explicit_number_rescues_an_unreadable_country(tmp_path: Path) -> None:
    """A Number carries its own country, which is more than the cells managed."""
    sample, problems, _ = builder.build(
        hybrid("whatsapp:+573005550420", country="Atlantis"), make_args(tmp_path)
    )
    assert problems == []
    assert sample["Number"].tolist() == ["whatsapp:+573005550420"]


def test_a_malformed_number_column_is_reported(tmp_path: Path) -> None:
    """A hand-typed Number is checked like any other."""
    _, problems, _ = builder.build(hybrid("whatsapp:+57 12345"), make_args(tmp_path))
    assert len(problems) == 1
    assert "Number column" in problems[0][1]


def test_organization_name_is_never_the_respondent_name(tmp_path: Path) -> None:
    """The opener greets a person, not an employer.

    "Organization Name" contains "name". While the form asks "First Name" the
    hint order saves us, but that holds only while the columns keep their
    current order and wording - so the role refuses the column outright.
    """
    columns = [
        "Timestamp",
        "Your name please",
        "Country code",
        "Your WhatsApp number",
        "Organization Name",
        FORM_COLUMNS[-1],
    ]
    frame = pd.DataFrame(
        [
            [
                "2026-08-24 09:00:00",
                "Ana",
                "+57",
                "3005550420",
                "IPA Colombia",
                CONSENT_TEXT,
            ]
        ],
        columns=columns,
    )
    assert (
        builder.pick_column(frame, builder.NAME_HINTS, "name", avoid=builder.NAME_AVOID)
        == "Your name please"
    )
    sample, problems, _ = builder.build(frame, make_args(tmp_path))
    assert problems == []
    assert sample["name"].tolist() == ["Ana"]


def test_the_consent_question_is_never_the_phone_column(tmp_path: Path) -> None:
    """The consent wording contains "WhatsApp", which is the phone role's hint.

    Column order is the only thing keeping these apart in the current export,
    and a form edit that moves the consent question ahead of the number would
    otherwise make every single row fail to resolve.
    """
    columns = [
        "Timestamp",
        "First Name",
        "Country code",
        FORM_COLUMNS[-1],
        "Your WhatsApp number",
        "Organization Name",
    ]
    frame = pd.DataFrame(
        [
            [
                "2026-08-24 09:00:00",
                "Ana",
                "+57",
                CONSENT_TEXT,
                "3005550420",
                "IPA",
            ]
        ],
        columns=columns,
    )
    assert (
        builder.pick_column(
            frame, builder.PHONE_HINTS, "phone", avoid=builder.PHONE_AVOID
        )
        == "Your WhatsApp number"
    )
    sample, problems, _ = builder.build(frame, make_args(tmp_path))
    assert problems == []
    assert sample["Number"].tolist() == ["whatsapp:+573005550420"]
