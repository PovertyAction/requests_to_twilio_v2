"""Build the bilingual data-use demonstration flow, in English and Spanish.

This is the repo's worked example: a randomised methods demonstration in which
participants are split into two arms that receive the *same four questions* in
different formats, and the close reveals the experiment.

* **ARM 1** is the counter-example: dense prose, cold register, no progress
  cues, and a demand to type the answer.
* **ARM 2** is the recommendation: the same four constructs in plain language,
  as a tappable list, warm in tone and paced so the respondent knows where they
  are.

ARM 2 is not merely the easier arm - it is the pattern this repo recommends, and
the session's conclusion. Every choice in it is deliberate and worth copying:
tappable options over typed input, one idea per message, "Question 2 of 4" so
break-off is a decision and not confusion, and a warm but neutral voice - no
persuasion, no urgency, no incentives. ARM 1 exists to make the size of that
difference measurable rather than asserted.

The contrast is the pedagogical payload: it shows how much of a "finding" is
really an artefact of how the question was asked. It first ran in Spanish at the
Foro Nacional de Datos in Bogota; the English instance runs at Research Staff
Training 2026 in Jaipur.

Why one builder for two languages
---------------------------------
Because the alternative already bit this account. Seven flows here share one
identical break-off path that never reaches the publish widget - the same defect
copied six times as flows were cloned from one another, six of them published.
Two hand-maintained language variants of the same instrument is that same
machine, waiting. Here the structure exists once and the language tables carry
only strings, so a fix to the graph fixes both languages and `check_flow` runs
over both on every build.

Why the options are list pickers and not "reply with a number"
--------------------------------------------------------------
ARM 2 used to *simulate* answer options by printing a numbered menu and asking
people to type the digit. WhatsApp has real interactive messages, and IPA's house
preference is buttons and lists over open text, so ARM 2 now sends
`twilio/list-picker` menus and consent sends `twilio/quick-reply` buttons.

The approval rules are what make this cheap, and they are worth stating plainly
because they drive the whole design:

===========================  ==============================  ====================
Content type                 In-session (24h window)         Business-initiated
===========================  ==============================  ====================
twilio/text                  free                            needs approval
twilio/quick-reply           free                            needs approval
twilio/list-picker           free                            **not supported**
===========================  ==============================  ====================

So only the *opener* needs Meta approval, because only the opener is sent before
the respondent has said anything. Consent and all eight questions land inside the
24-hour window the opener's reply opens, so their templates are created and used
without ever being submitted. A list picker sent as the first message fails with
error 63016 - `check_flow` has a check for exactly that.

Answers still arrive as text, so the splits accept **tap or type**: a respondent
who ignores the menu and writes "1" is matched just the same. That also keeps the
retry machinery meaningful - it now only fires for genuinely unparsable typed
input, which is itself one of the findings the demo shows off.

Why the splits use `regex` and not `matches_any_of`
---------------------------------------------------
Because `matches_any_of` takes its alternatives as a single comma-delimited
string. A comma inside an option label silently becomes two alternatives,
neither of which is the label, and the respondent taps a real option and lands
on noMatch. In a regex a comma is just a character. Regex also lets the digit
form tolerate the punctuation people actually type - "1.", "1)", "(1)".

Both predicates are already case-insensitive and already trim surrounding
whitespace, so neither is what breaks.

The deeper fix is not the predicate though. It is that `check_language` **runs**
every condition it generates - every label, every digit, and a handful of things
nobody would ever tap - and checks where each one lands. A condition that looks
right and matches nothing is the same class of defect as a break-off that
publishes no row: invisible in the editor, obvious only in the data.

Run with `just build-demo-flow`.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from twilio.rest import Client  # noqa: E402

from requests_to_twilio import config as cfg  # noqa: E402
from requests_to_twilio import templates as tpl  # noqa: E402

# The option-matching helpers live in the package rather than here because the
# survey spec validator needs the same judgements, and a script is not
# importable. Re-exported under their original names so this module reads as it
# always did - and so `demo.answer_pattern` in the tests keeps resolving.
from requests_to_twilio.answers import (  # noqa: E402
    _STRIPPED_PUNCTUATION,
    answer_pattern,
    code_mapping,
    escape_literal,
    expected_code,
    has_emoji,
    normalise_reply,
    option_code,
    positions_are_ambiguous,
    word_pattern,
)
from requests_to_twilio.flows import check_flow, evaluate_condition  # noqa: E402
from requests_to_twilio.outcomes import (  # noqa: E402
    ENCRYPTION_FAILED_STATUS,
    FINAL_STATUS_NOTES,
    FINAL_STATUSES,
    final_status_liquid,
)
from requests_to_twilio.spec import DEFAULT_CONSTRAINTS  # noqa: E402

#: Kept as the private alias this module used before the move.
_has_emoji = has_emoji

__all__ = [
    "answer_pattern",
    "code_mapping",
    "escape_literal",
    "expected_code",
    "normalise_reply",
    "option_code",
    "positions_are_ambiguous",
    "word_pattern",
]

FLOW_DIR = REPO_ROOT / "flows"
TEMPLATE_DIR = REPO_ROOT / "templates" / "generated"
CODEBOOK_DIR = REPO_ROOT / "codebook"

FROM = "{{flow.channel.address}}"
TIMEOUT = "3600"  # 1 hour, as in the source flow

#: Deployed Functions service. Both languages share it: the encryption and
#: publish code is language-independent, and duplicating it would reintroduce
#: exactly the drift this builder exists to prevent.
#: The Functions service `just deploy-functions` creates. Everything about the
#: deployment is looked up from this one name at build time rather than pasted
#: in as SIDs: a `ZS`/`ZE`/`ZH` triple and a `*.twil.io` host are per-account,
#: and the domain carries a random suffix that cannot even be guessed. Hard-
#: coding them meant `just build-demo-flow` emitted a flow pointing at the
#: author's own subaccount, which 404s for everybody else - the single thing
#: that stopped another team running this.
FUNCTIONS_SERVICE_NAME = "rtt-survey"
ENCRYPT_FUNCTION_NAME = "encrypt_fields"

#: The path the encryption function is deployed at, from
#: `deploy_twilio_functions.py`.
ENCRYPT_PATH = "/encrypt-fields"

#: Where a submission is written, and therefore which Function the last widget
#: calls. Both destinations are real and both are supported; they differ in what
#: the person running the round has to obtain first.
#:
#:   motherduck  an INSERT over the Postgres wire protocol. No column ceiling,
#:               no per-write quota, and the data lands where analysis already
#:               happens. Needs a MotherDuck account and a token.
#:   gsheets     an append to a spreadsheet's next row. The lowest barrier there
#:               is - a sheet, a service account, and anybody on the team can
#:               open the result and read it - which is why it stays a
#:               first-class path rather than a legacy one. Needs a Google Cloud
#:               service account, and carries the Sheets API quota and the
#:               172-column ceiling of a header-row lookup.
#:
#: The widget name is part of the target because it is what a reviewer reads on
#: the Studio canvas, and a canvas that says `publish_motherduck` while writing
#: to a spreadsheet is worse than no label at all. Every name here starts with
#: `publish_`, which is what `rtt flow check` matches on to find the step that
#: writes the row - see `_is_publish_widget` in flows.py.
PUBLISH_TARGETS: dict[str, dict[str, str]] = {
    "motherduck": {
        "function": "publish_motherduck",
        "path": "/publish-motherduck",
        "widget": "publish_motherduck",
    },
    "gsheets": {
        "function": "publish_gsheets",
        "path": "/publish-gsheets",
        "widget": "publish_gsheets",
    },
}

#: What `just build-demo-flow` writes when told nothing.
#:
#: Google Sheets, because this is the choice a new user should not have to make
#: before their first round. A spreadsheet and a service account is the shortest
#: path from "cloned the repo" to "watched a reply arrive", and everyone on a
#: team can open the result without being taught a query language. MotherDuck is
#: better for a long instrument or for analysis that outlives collection, and it
#: is one flag away.
#:
#: Neither is the right answer for everybody - this is a per-round decision, and
#: `--publish-target` is how it is made. What a default settles is only which
#: one you get by not deciding.
DEFAULT_PUBLISH_TARGET = "gsheets"

QUESTION_KEYS = ("P1", "P2", "P3", "P4", "P5", "P6")

#: How an ARM 2 question is rendered, and therefore which widgets and which
#: content template it becomes. ARM 1 ignores this entirely: every ARM 1 question
#: is open text, which is the comparison the demo exists to make.
#:
#: ``list``    ``twilio/list-picker``, 1-10 options, tap or type the position
#: ``button``  ``twilio/quick-reply``, 1-3 options, tap or type the position
#: ``integer`` no template at all - a plain body, reply validated by regex
#:
#: ``integer`` is the odd one out and the reason `kind` exists rather than being
#: inferred from the presence of options: it has none, so nothing about the
#: table's shape distinguishes it from a question somebody forgot to fill in.
QUESTION_KINDS = ("list", "button", "integer")

#: The caps Twilio enforces on interactive content. A list-picker with 11 rows
#: is rejected at create time, which during a round means the flow cannot be
#: rebuilt - so `check_language` refuses first, at build time, where it is cheap.
MAX_LIST_OPTIONS = 10
MAX_BUTTON_OPTIONS = 3

#: Studio has no `now` variable - `{{flow.variables.now}}` renders empty, which
#: is how set_time_fin came to be blank on every row of the first live test. The
#: Liquid date filter does accept the literal string 'now', which is the
#: documented way to stamp a time.
#:
#: The flow no longer stamps times at all, and this is why.
#:
#: Studio renders `now` in Twilio's own timezone, not UTC - measured live, a
#: stamp of 09:12:05 sat beside a submitted_at of 16:12:06 UTC in the same row.
#: Liquid cannot convert it: the date filter has no timezone directive, and the
#: %s (epoch) escape is not supported either. Asking for %s does not error, it
#: writes the literal string "%s" into the column, which a live round proved.
#:
#: There is no way to produce a UTC timestamp from inside a Studio widget, so
#: the flow stops pretending. Both ends come from sources that are UTC by
#: construction:
#:
#:   start     execution.date_created, from the Studio API via `rtt fetch`
#:   end       submitted_at, stamped server-side by the publish Function
#:
#: `execution_sid` is published so the two join exactly. That column earns its
#: place regardless - without it a warehouse row cannot be traced back to the
#: execution log that produced it.


# ---------------------------------------------------------------------------
# Language tables.
#
# Everything below is strings. No structure, no transitions, no widget names -
# those live once, in build(). An option's label appears exactly once and is
# used for the list-picker item, the split condition and the code mapping, so
# the message a respondent sees and the value stored for them cannot disagree.
#
# check_language() enforces the limits on this text, and does it by *executing*
# the conditions it generates rather than by checking style rules:
#
#   * Length. A list-picker item title caps at 24 characters, its description at
#     72, a quick-reply button title at 25, and the list button at 20.
#   * Reachability. Every label and every position typed as a digit is pushed
#     through the generated pattern and must route to the store widget; junk
#     must not. A pattern loose enough to accept anything is worse than one that
#     accepts too little - it stores junk as a real answer and never re-asks.
#   * Agreement. Whatever the split accepts, the code mapping must code as that
#     option, not as `other`.
#
# Commas in labels are fine. They were not when the splits used matches_any_of,
# whose alternatives are one comma-delimited string; the regex predicate has no
# delimiter, which is the main reason for the switch.
#
# The one thing still forbidden by rule rather than by test is EMOJI IN A LABEL.
# Labels are compared literally against the reply body, so every byte has to
# survive a round trip through WhatsApp, Studio and the condition - and
# skin-tone and variation selectors make two visually identical labels different
# strings. A test cannot catch that, because it compares the string to itself.
# Warmth belongs in the message body, which nothing matches on; labels stay
# boring on purpose.
# ---------------------------------------------------------------------------

EN: dict[str, Any] = {
    "name": "English",
    "flow_suffix": "en",
    "language": "en",
    # The two ends of the bookend, and the only two templates in this flow that
    # Meta ever sees. Everything between them is in session and free.
    "intro_template": "rst2026_intro",
    "close_template": "rst2026_close",
    "description": "Data use demo (ARM1/ARM2 experiment) - English, RST Jaipur 2026",
    "consent": {
        "body": (
            "👋 Before we start - would you like to take part?\n\n"
            "It takes about 3 minutes. Taking part is voluntary: you can stop "
            "at any time by not replying, and your answers are confidential."
        ),
        # Commas are fine in a label now that the splits use regex; under
        # matches_any_of these had to read "Yes I will take part".
        "button_yes": "Yes, I'll take part",
        "button_no": "No, thanks",
        "typed_yes": "1|yes|y",
        "typed_no": "2|no|n",
    },
    # Recognised at every question, in either arm. Twilio's own opt-out handling
    # covers the carrier keywords for SMS; inside a WhatsApp session a "STOP"
    # arrives as an ordinary reply, and without this it is stored as the answer
    # to whatever was asked - then the next question is sent anyway.
    "stop_words": ["stop", "quit", "unsubscribe", "cancel", "end"],
    "stop_ack": (
        "Understood - I have stopped here and will not send anything else "
        "about this survey.\n\n"
        "The answers you already gave are kept, and I will not ask you "
        "anything more. Thank you for your time."
    ),
    # ARM 1 - a competent face-to-face questionnaire, on the wrong platform.
    #
    # These are NOT badly written. That is the entire point, and the thing the
    # session turns on. Each one does the job a survey methodologist would ask
    # of it: a dated reference frame so every respondent counts the same window,
    # exhaustive enumeration so nothing is ambiguous, an explicit single-versus-
    # multiple answer instruction, and an interviewer note carrying the probing
    # rules. Every one of those is correct practice for CAPI.
    #
    # Every one of them also breaks here. There is no interviewer to read the
    # note, no showcard to read aloud from, and no patience for a 60-word frame
    # on a phone screen somebody is reading between sessions. ARM 2 is better
    # not because it is written better, but because it was written for this
    # channel.
    #
    # P5 is the exception: it is a genuinely bad question, deliberately. See
    # the note above it.
    #
    # WARNING - THESE DATES ARE THE AUGUST 2026 RST JAIPUR SESSION.
    # The session runs Sunday 23 to Friday 28 August 2026 and presents on
    # Wednesday 26 August at 14:00, so "earlier today" is Wednesday's lunch and
    # "yesterday" is Tuesday the 25th. Move the training and all five of these
    # strings are wrong - specifically, silently wrong, because a reference
    # frame that names the wrong day still reads as a well-formed question.
    "arm1": {
        # The whole week, including the days that have not happened when this
        # is sent at 14:00 on the Wednesday. "is, or you expect will be" is how
        # a CAPI question covers an open window without leading the respondent
        # toward the days already held.
        "P1": (
            "Thinking about the training week as a whole, that is the period "
            "running from Sunday the 23rd of August to Friday the 28th of "
            "August inclusive, and taking into consideration all of its "
            "components together - the plenary sessions, the practical "
            "exercises, the group work and the informal exchanges between "
            "them - which single day of that week would you say is, or you "
            "expect will be, the most rewarding for you personally?\n\n"
            "[INTERVIEWER: Read out the days one at a time. Record one day "
            "only. If the respondent names more than one, probe for the single "
            "most rewarding.]\n\n"
            "_Reply with the day._"
        ),
        "P2": (
            "During the midday meal break held earlier today, Wednesday the "
            "26th of August, immediately preceding the present session, did "
            "the meal that you yourself consumed include a dessert - "
            "understood as any sweet course, confection, pastry, fruit "
            "preparation or other sweet item - whether taken as part of that "
            "meal or immediately following it?\n\n"
            "[INTERVIEWER: Do not read the response options. Code 'yes' if "
            "the respondent reports any sweet item, however small.]\n\n"
            "_Reply yes or no._"
        ),
        "P3": (
            "Considering the whole of yesterday, Tuesday the 25th of August, "
            "from the time you woke until the time you retired for the night, "
            "and counting each serving separately irrespective of its size, "
            "its preparation or where it was obtained, how many servings of "
            "coffee or tea did you consume in total over the course of that "
            "day?\n\n"
            "[INTERVIEWER: Record the exact figure. If the respondent is "
            "unable to recall precisely, probe for a best estimate.]\n\n"
            "_Reply with an exact number (e.g. 0, 1, 3)._"
        ),
        # The multi-answer question. ARM 1 can ask it - ARM 2 cannot - and the
        # price is a column of strings somebody has to clean by hand. Expect
        # "Stata,R", "stata and R", "all of them", "python (a bit)". That is the
        # demo: the construct survives and the variable does not.
        #
        # "Read the list aloud" is the sharpest line in the arm. There is no
        # list - ARM 1 is open text - so the instruction refers to a showcard
        # that does not exist, in a message read by the respondent rather than
        # by an interviewer. It is exactly the artefact a CAPI instrument leaves
        # behind when it is pasted into a self-administered channel.
        "P4": (
            "Across the full range of software tools that you have occasion to "
            "employ in the course of your professional duties, whether on a "
            "routine basis or only intermittently, and including those used "
            "for data collection, data management, analysis or reporting, "
            "please indicate every one with which you would describe yourself "
            "as comfortable working without assistance.\n\n"
            "[INTERVIEWER: Read the list aloud. Record all that apply. Do not "
            "prompt for tools the respondent does not mention.]\n\n"
            "_Reply with all that apply, separated by commas (for example: "
            "Stata, R)._"
        ),
        # The one genuinely bad question, and the only one in either arm that
        # is short. Double-barrelled (satisfaction AND recommendation, which
        # cannot both be answered by one reply), jargon nobody outside the
        # trade uses - "instrument", "modality", "data collection exercises" -
        # and no reference frame or scale at all.
        #
        # It therefore does NOT measure what ARM 2's P5 measures, and that is
        # the second deliberate divergence after P4. The cost of double-
        # barrelling is not only a column that is hard to code; it is a column
        # that cannot be lined up against anything, so the arm comparison this
        # whole demo exists to make is unavailable on exactly this question.
        # Say that out loud in the session - it is the reason the question is
        # here.
        "P5": (
            "How satisfied are you with the usability and relevance of this "
            "instrument, and would you recommend its modality for future data "
            "collection exercises?\n\n"
            "_Reply in your own words._"
        ),
        # The scheduling question, and the one whose cost is visible within
        # minutes rather than at analysis. ARM 1 asks for a date and a time as
        # free text, so the column arrives as "28th around 3", "tomorrow
        # evening", "not sure yet" - every one a fair answer, none of them a
        # slot anybody can book against without reading it first.
        #
        # It also carries the leftover interviewer instruction, for the same
        # reason P4 does: probing is something an interviewer does, and there is
        # no interviewer.
        "P6": (
            "In relation to your onward travel arrangements following the "
            "conclusion of the training at the end of this week, and taking "
            "into consideration any connecting services, transfers or "
            "other scheduling constraints that may apply to your itinerary, "
            "please indicate the window during which you anticipate departing "
            "from Jaipur.\n\n"
            "[INTERVIEWER: Record the date and the approximate time. If the "
            "respondent is uncertain, probe for the most likely window.]\n\n"
            "_Reply with your expected departure date and time._"
        ),
    },
    # ARM 2 - the recommended pattern. The same five constructs, as tappable
    # lists, one idea per message, with a progress cue so the respondent always
    # knows how much is left. Emoji are used sparingly and only in bodies.
    #
    # THE RULE THESE BODIES FOLLOW: ARM 2 keeps every methodological commitment
    # ARM 1 makes and spends fewer words on it. Short is not the goal - short
    # *with the reference frame intact* is the goal, and the difference between
    # the arms is register and response format, never rigour.
    #
    # This matters because of how the comparison can fail. If ARM 2 quietly
    # drops the frame - "how many coffees did you have?" against ARM 1's dated,
    # bounded, whole-day version - then its cleaner answers are explained by
    # having asked an easier question, and the session's finding evaporates
    # under the first methodologist to raise a hand. ARM 2 has to win on format
    # while asking the same question. So P1 carries the whole-week frame and
    # the not-yet-happened hedge, P3 says which day and that it is the whole of
    # it, and P4 keeps ARM 1's "without assistance" criterion as "on your own".
    # Each is longer than the shortest phrasing available, on purpose.
    "arm2": {
        "button": "Choose an answer",
        "P1": {
            "kind": "list",
            "body": (
                "📅 Question 1 of 6\n\n"
                "Which day of the training week has been - or you think will "
                "be - your favourite?"
            ),
            "options": [
                ("p1_sun", "Sunday", "Day 1"),
                ("p1_mon", "Monday", "Day 2"),
                ("p1_tue", "Tuesday", "Day 3"),
                ("p1_wed", "Wednesday", "Day 4"),
                ("p1_thu", "Thursday", "Day 5"),
                ("p1_fri", "Friday", "Day 6"),
            ],
        },
        "P2": {
            "kind": "button",
            "body": ("🍰 Question 2 of 6\n\nDid you have dessert at lunch today?"),
            # Quick-reply actions carry a title and an id; the third element is
            # unused on screen and shows up only in the text fallback. Kept as a
            # triple so the pattern and the code mapping treat every question
            # kind identically.
            "options": [
                ("p2_yes", "Yes", "I had dessert"),
                ("p2_no", "No", "No dessert today"),
            ],
        },
        # No options: an integer question is a plain body and a regex.
        # Bounded, like every other ARM 2 answer. The arm's premise is that a
        # reply lands in a known set, and "any whole number" is not one - it
        # accepts 900 as readily as 2. 0-10 covers every honest answer to this
        # question and refuses the rest, so the column needs no cleaning.
        "P3": {
            "kind": "integer",
            "constraint": r"(?:\s*(?:10|[0-9])\s*)",
            "accepts": [str(n) for n in range(11)],
            "refuses": ["11", "100", "-1", "two", "about 3", "3.5", ""],
            "body": (
                "☕ Question 3 of 6\n\n"
                "How many cups of coffee or tea did you have during the whole "
                "day yesterday?\n\n"
                "_Reply with a number from 0 to 10._"
            ),
        },
        # ARM 2 cannot ask this as a multi-answer question - WhatsApp only offers
        # MULTI_SELECT through twilio/flows, which needs Meta approval even in
        # session. So the variable stays clean and the question narrows to one
        # answer, where ARM 1 keeps the question and gives up the variable.
        "P4": {
            "kind": "list",
            "body": (
                "💻 Question 4 of 6\n\n"
                "Which of these are you *most* comfortable using on your own?"
            ),
            "options": [
                ("p4_python", "Python", "pandas, scripts, notebooks"),
                ("p4_stata", "Stata", "do-files and .dta data"),
                ("p4_r", "R", "tidyverse and RStudio"),
                ("p4_surveycto", "SurveyCTO", "Form design and data collection"),
                ("p4_twilio", "Twilio", "Studio flows and WhatsApp"),
                ("p4_exotel", "Exotel", "Voice and IVR surveys"),
                ("p4_office", "Microsoft Office", "Excel, Word and PowerPoint"),
            ],
        },
        # The scale is carried by the descriptions, not the titles. A title is
        # compared literally against the reply body, and an emoji there would be
        # a tap that matches nothing - see the note above the language tables.
        "P5": {
            "kind": "list",
            "body": (
                "🙌 Question 5 of 6\n\nDid you enjoy answering this survey?"
            ),
            "options": [
                ("p5_loved", "Loved it", "🤩 Best thing all week"),
                ("p5_liked", "Liked it", "🙂 Good, I would do it again"),
                ("p5_fine", "It was fine", "😐 No strong feelings"),
                ("p5_meh", "Not really", "🙁 A bit tedious"),
                ("p5_disliked", "Did not like it", "😞 I would rather not"),
            ],
        },
        # Ten rows, exactly the cap. Discrete departure times rather than
        # halves of a day, because the question is mimicking a scheduler and a
        # scheduler offers slots. The gaps between them are the point: a
        # schedule shows what is available, not every minute that exists.
        #
        # The last row is the escape hatch, and it is not optional. A list
        # picker refuses anything that is not an option, so a respondent with no
        # applicable slot would loop through the retry twice and land on giveup
        # - the failure this question exists to demonstrate against rather than
        # to commit.
        #
        # Times are invented. This mimics scheduling; it books nothing.
        #
        # Titles carry the slot and descriptions the clock, because a title is
        # compared literally against the reply body. Titles stay under 24
        # characters and carry no emoji, both of which are rejected at
        # template-create time.
        "P6": {
            "kind": "list",
            "body": (
                "✈️ Last one - question 6 of 6\n\n"
                "When do you expect to leave Jaipur after the training?"
            ),
            "options": [
                ("p6_fri_1800", "Fri 18:00", "Friday 28 August"),
                ("p6_fri_2115", "Fri 21:15", "Friday 28 August"),
                ("p6_sat_0620", "Sat 06:20", "Saturday 29 August"),
                ("p6_sat_0945", "Sat 09:45", "Saturday 29 August"),
                ("p6_sat_1430", "Sat 14:30", "Saturday 29 August"),
                ("p6_sat_2050", "Sat 20:50", "Saturday 29 August"),
                ("p6_sun_0705", "Sun 07:05", "Sunday 30 August"),
                ("p6_sun_1120", "Sun 11:20", "Sunday 30 August"),
                ("p6_sun_1915", "Sun 19:15", "Sunday 30 August"),
                ("p6_staying", "Staying on", "Leaving after Sunday"),
            ],
        },
    },
    # States the bound the split actually enforces. A nudge that says "a number"
    # after refusing 42 tells the respondent they did as they were asked and were
    # still wrong, which is how somebody abandons a question they can answer.
    "error_numeric": (
        "Please reply with a number from 0 to 10.\n\n"
        "_Just the digits, for example 0, 2 or 10._\n\n"
        "I am a bot, so a plain number is all I can read here."
    ),
    # A quick-reply question has buttons and no list, so the list nudge would
    # name a control that is not on screen.
    "error_button": (
        "No problem - I could not read that one.\n\n"
        "Tap one of the buttons on the message above. You can also just reply "
        "with the number of your answer.\n\n"
        "I am a bot, so I only understand the options."
    ),
    # {button} is filled in from the same table entry the list picker uses, so
    # the nudge can never name a button that is not on screen.
    "error_option": (
        "No problem - I could not read that one.\n\n"
        "Tap *{button}* on the message above and pick from the list. You can "
        "also just reply with the number of your answer.\n\n"
        "I am a bot, so I only understand the options."
    ),
    # For questions whose options are themselves numbers. Inviting "reply with
    # the number" there asks for exactly the reply the split has to refuse: on
    # a 0/1/2-3 projects scale a "1" could be the position or the label, and
    # they are different options.
    "error_option_labels": (
        "No problem - I could not read that one.\n\n"
        "Tap *{button}* on the message above and pick from the list, or type "
        "the option exactly as it appears.\n\n"
        "I am a bot, so I only understand the options."
    ),
    # Sent straight after P6, echoing the slot back. ARM 2 can name it because
    # the reply maps to a known option; ARM 1 can only repeat what was typed.
    # {slot} is filled in by the flow builder, differently per arm.
    "confirm_p6": (
        "🧳 Noted: *{slot}*.\n\n"
        "Remember to pack your things and be ready in good time for your flight."
    ),
    "close_complete": (
        "🙏 Thank you for completing the survey.\n\n"
        "You took part in an experiment with two versions of the same survey: "
        "one asking openly in dense prose, and one asking the same things in "
        "plain language with tappable answers. You saw one of them.\n\n"
        "The second version is the one we recommend, and in the session we will "
        "show what your group's answers looked like - and how much the format "
        "alone changed them.\n\n"
        "See you there."
    ),
    "close_declined": (
        "Thank you for your reply. We understand and respect your decision not "
        "to take part in the exercise.\n\n"
        "You are still very welcome in the session, where we will look at how "
        "WhatsApp data collection works and where it goes wrong."
    ),
    "close_incomplete": (
        "Thank you for the answers you gave - they are recorded.\n\n"
        "We will look at how the format of a question changes the answers it "
        "gets, live in the session. See you there."
    ),
    # Sent to anyone who writes to the number without being launched into the
    # survey - which in practice is mostly people saying thank you after
    # finishing it.
    "unsolicited": (
        "👋 Thanks for your message.\n\n"
        "This number only runs a short survey during the session, so there is "
        "nothing further needed from you here.\n\n"
        "If you have a question, the IPA team at the training can help."
    ),
}

ES: dict[str, Any] = {
    "name": "Spanish",
    "flow_suffix": "es",
    "language": "es",
    "intro_template": "data_use_demo_intro_es",
    "close_template": "data_use_demo_close_es",
    "description": "Data use demo (ARM1/ARM2 experiment) - Spanish",
    "consent": {
        "body": (
            "👋 Antes de empezar - ¿quieres participar?\n\n"
            "Toma unos 3 minutos. La participación es voluntaria: puedes "
            "dejar de responder en cualquier momento y tus respuestas son "
            "confidenciales."
        ),
        "button_yes": "Sí, participo",
        "button_no": "No, gracias",
        "typed_yes": "1|si|sí|s",
        "typed_no": "2|no|n",
    },
    # Los términos en inglés también se reconocen: mucha gente escribe "stop"
    # sin importar el idioma de la encuesta.
    "stop_words": [
        "stop",
        "parar",
        "cancelar",
        "salir",
        "baja",
        "no molestar",
        "quit",
    ],
    "stop_ack": (
        "Entendido - me detengo aquí y no te enviaré nada más sobre esta "
        "encuesta.\n\n"
        "Las respuestas que ya diste se conservan, y no te preguntaré nada "
        "más. Gracias por tu tiempo."
    ),
    # ARM 1 - un cuestionario presencial competente, en la plataforma
    # equivocada. Mismas marcas de género que la tabla inglesa y por las mismas
    # razones: marco de referencia fechado, enumeración exhaustiva, instrucción
    # explícita de respuesta única o múltiple, y una nota al encuestador que
    # aquí no tiene a quién dirigirse. Ver la nota extensa sobre EN["arm1"].
    #
    # ADVERTENCIA - ESTAS FECHAS SON LA SESIÓN DE AGOSTO DE 2026.
    # La semana va del domingo 23 al viernes 28 de agosto de 2026 y se presenta
    # el miércoles 26 a las 14:00. Cambiar la formación invalida las cinco
    # preguntas en silencio: un marco que nombra el día equivocado sigue
    # leyéndose como una pregunta bien formada.
    "arm1": {
        "P1": (
            "Pensando en la semana de formación en su conjunto, es decir el "
            "período comprendido entre el domingo 23 de agosto y el viernes 28 "
            "de agosto inclusive, y tomando en consideración todos sus "
            "componentes - las sesiones plenarias, los ejercicios prácticos, "
            "el trabajo en grupo y los intercambios informales entre ellos - "
            "¿qué día de esa semana señalarías como el que es, o esperas que "
            "sea, el más provechoso para ti personalmente?\n\n"
            "[ENCUESTADOR: Lea los días uno por uno. Registre un solo día. Si "
            "la persona menciona más de uno, indague por el más provechoso.]\n\n"
            "_Responde con el día._"
        ),
        "P2": (
            "Durante la pausa del almuerzo de hoy, miércoles 26 de agosto, "
            "inmediatamente anterior a la presente sesión, ¿la comida que "
            "consumiste incluyó un postre - entendido como cualquier plato "
            "dulce, confitería, repostería, preparación de fruta u otro "
            "elemento dulce - ya sea como parte de esa comida o "
            "inmediatamente después de ella?\n\n"
            "[ENCUESTADOR: No lea las opciones de respuesta. Codifique 'sí' si "
            "la persona reporta cualquier elemento dulce, por pequeño que "
            "sea.]\n\n"
            "_Responde sí o no._"
        ),
        "P3": (
            "Considerando la totalidad del día de ayer, martes 25 de agosto, "
            "desde que despertaste hasta que te retiraste a dormir, y contando "
            "cada porción por separado sin importar su tamaño, su preparación "
            "o dónde fue obtenida, ¿cuántas porciones de café o té consumiste "
            "en total a lo largo de ese día?\n\n"
            "[ENCUESTADOR: Registre la cifra exacta. Si la persona no logra "
            "recordarla con precisión, indague por su mejor estimación.]\n\n"
            "_Responde con un número exacto (ej.: 0, 1, 3)._"
        ),
        # La pregunta de respuesta múltiple. El ARM 1 puede hacerla; el ARM 2
        # no. El precio es una columna de texto que alguien debe limpiar a mano.
        #
        # "Lea la lista en voz alta" es la línea más aguda del brazo: no hay
        # lista - el ARM 1 es texto abierto - así que la instrucción remite a
        # una tarjeta que no existe, en un mensaje que lee la persona
        # encuestada y no un encuestador.
        "P4": (
            "Entre el conjunto de herramientas informáticas que tienes ocasión "
            "de emplear en el ejercicio de tus funciones profesionales, ya sea "
            "de forma habitual o solo intermitente, e incluyendo las utilizadas "
            "para recolección de datos, gestión de datos, análisis o "
            "elaboración de informes, indica todas aquellas con las que te "
            "describirías como capaz de trabajar sin asistencia.\n\n"
            "[ENCUESTADOR: Lea la lista en voz alta. Registre todas las que "
            "apliquen. No sugiera herramientas que la persona no mencione.]\n\n"
            "_Responde con todos los que apliquen, separados por comas (por "
            "ejemplo: Stata, R)._"
        ),
        # La única pregunta genuinamente mala, y la única corta de cualquiera de
        # los dos brazos. Doble cañón (satisfacción Y recomendación, que una
        # sola respuesta no puede contestar), jerga que nadie fuera del gremio
        # usa - "instrumento", "modalidad", "ejercicios de recolección" - y sin
        # marco de referencia ni escala.
        #
        # Por tanto NO mide lo que mide el P5 del ARM 2, y esa es la segunda
        # divergencia deliberada después del P4. El costo del doble cañón no es
        # solo una columna difícil de codificar: es una columna que no puede
        # compararse contra nada, así que justo en esta pregunta la comparación
        # entre brazos no está disponible.
        "P5": (
            "¿Qué tan satisfecha o satisfecho estás con la usabilidad y la "
            "pertinencia de este instrumento, y recomendarías su modalidad "
            "para futuros ejercicios de recolección de datos?\n\n"
            "_Responde con tus propias palabras._"
        ),
        # La pregunta de agenda, y la única cuyo costo se ve en minutos y no en
        # el análisis. El ARM 1 pide fecha y hora como texto libre, así que la
        # columna llega como "el 28 como a las 3", "mañana en la noche", "aún no
        # sé": todas respuestas razonables, ninguna un horario reservable sin
        # que alguien lo lea primero.
        "P6": (
            "En relación con tus arreglos de viaje posteriores a la "
            "finalización de la formación al término de esta semana, y "
            "considerando cualquier conexión, traslado u otra restricción de "
            "horario que pudiera aplicar a tu itinerario, por favor indica la "
            "ventana durante la cual prevés partir de Jaipur.\n\n"
            "[ENCUESTADOR: Registre la fecha y la hora aproximada. Si la "
            "persona no está segura, indague por la ventana más probable.]\n\n"
            "_Responde con la fecha y hora estimadas de tu salida._"
        ),
    },
    # ARM 2 - misma regla que en inglés: conserva todos los compromisos
    # metodológicos del ARM 1 y gasta menos palabras en ellos. Corto NO es el
    # objetivo; corto *con el marco de referencia intacto* sí lo es. Si el ARM 2
    # deja caer el marco, sus respuestas más limpias se explican por haber hecho
    # una pregunta más fácil, y el hallazgo de la sesión se desarma ante la
    # primera persona que levante la mano.
    "arm2": {
        "button": "Elige tu respuesta",
        "P1": {
            "kind": "list",
            "body": (
                "📅 Pregunta 1 de 6\n\n"
                "¿Cuál es - o crees que será - tu día favorito de la semana "
                "de formación?"
            ),
            "options": [
                ("p1_sun", "Domingo", "Día 1"),
                ("p1_mon", "Lunes", "Día 2"),
                ("p1_tue", "Martes", "Día 3"),
                ("p1_wed", "Miércoles", "Día 4"),
                ("p1_thu", "Jueves", "Día 5"),
                ("p1_fri", "Viernes", "Día 6"),
            ],
        },
        "P2": {
            "kind": "button",
            "body": ("🍰 Pregunta 2 de 6\n\n¿Tomaste postre en el almuerzo de hoy?"),
            "options": [
                ("p2_yes", "Sí", "Sí tomé postre"),
                ("p2_no", "No", "Hoy no tomé postre"),
            ],
        },
        "P3": {
            "kind": "integer",
            "constraint": r"(?:\s*(?:10|[0-9])\s*)",
            "accepts": [str(n) for n in range(11)],
            "refuses": ["11", "100", "-1", "dos", "como 3", "3,5", ""],
            "body": (
                "☕ Pregunta 3 de 6\n\n"
                "¿Cuántas tazas de café o té tomaste durante todo el día de "
                "ayer?\n\n"
                "_Responde con un número del 0 al 10._"
            ),
        },
        "P4": {
            "kind": "list",
            "body": (
                "💻 Pregunta 4 de 6\n\n"
                "¿Cuál de estos manejas con *más* comodidad por tu cuenta?"
            ),
            "options": [
                ("p4_python", "Python", "pandas, scripts, cuadernos"),
                ("p4_stata", "Stata", "archivos do y datos .dta"),
                ("p4_r", "R", "tidyverse y RStudio"),
                ("p4_surveycto", "SurveyCTO", "Diseño de formularios y recolección"),
                ("p4_twilio", "Twilio", "Flujos de Studio y WhatsApp"),
                ("p4_exotel", "Exotel", "Encuestas de voz e IVR"),
                ("p4_office", "Microsoft Office", "Excel, Word y PowerPoint"),
            ],
        },
        # La escala va en las descripciones, no en los títulos: un título se
        # compara literalmente con el cuerpo de la respuesta.
        "P5": {
            "kind": "list",
            "body": ("🙌 Pregunta 5 de 6\n\n¿Disfrutaste esta encuesta?"),
            "options": [
                ("p5_loved", "Me encantó", "🤩 Lo mejor de la semana"),
                ("p5_liked", "Me gustó", "🙂 Bien, la repetiría"),
                ("p5_fine", "Estuvo bien", "😐 Sin opinión fuerte"),
                ("p5_meh", "No mucho", "🙁 Algo tediosa"),
                ("p5_disliked", "No me gustó", "😞 Preferiría no hacerla"),
            ],
        },
        # Diez filas, justo el tope. Horas concretas y no mitades del día,
        # porque la pregunta imita un agendador y un agendador ofrece franjas.
        # Los huecos entre ellas son el punto: una agenda muestra lo disponible.
        #
        # La última fila es la salida de emergencia y no es opcional: un selector
        # de lista rechaza lo que no sea una opción, así que alguien sin franja
        # aplicable daría vueltas en el reintento hasta caer en giveup.
        "P6": {
            "kind": "list",
            "body": (
                "✈️ La última - pregunta 6 de 6\n\n"
                "¿Cuándo esperas salir de Jaipur después de la formación?"
            ),
            "options": [
                ("p6_fri_1800", "Vie 18:00", "Viernes 28 de agosto"),
                ("p6_fri_2115", "Vie 21:15", "Viernes 28 de agosto"),
                ("p6_sat_0620", "Sáb 06:20", "Sábado 29 de agosto"),
                ("p6_sat_0945", "Sáb 09:45", "Sábado 29 de agosto"),
                ("p6_sat_1430", "Sáb 14:30", "Sábado 29 de agosto"),
                ("p6_sat_2050", "Sáb 20:50", "Sábado 29 de agosto"),
                ("p6_sun_0705", "Dom 07:05", "Domingo 30 de agosto"),
                ("p6_sun_1120", "Dom 11:20", "Domingo 30 de agosto"),
                ("p6_sun_1915", "Dom 19:15", "Domingo 30 de agosto"),
                ("p6_staying", "Me quedo", "Salgo después del domingo"),
            ],
        },
    },
    "error_numeric": (
        "Por favor responde con un número del 0 al 10.\n\n"
        "_Solo los dígitos, por ejemplo 0, 2 o 10._\n\n"
        "Soy un robot, así que aquí solo puedo leer un número."
    ),
    "error_button": (
        "Sin problema - no pude leer esa respuesta.\n\n"
        "Toca uno de los botones del mensaje de arriba. También puedes "
        "responder con el número de tu respuesta.\n\n"
        "Soy un robot, así que solo entiendo las opciones."
    ),
    "error_option": (
        "Sin problema - no pude leer esa respuesta.\n\n"
        "Toca *{button}* en el mensaje anterior y selecciona de la lista. "
        "También puedes responder con el número de tu "
        "respuesta.\n\n"
        "Soy un robot, así que solo entiendo las opciones."
    ),
    # Para preguntas cuyas opciones ya son números. Invitar "responde con el
    # número" ahí pide justo la respuesta que el split tiene que rechazar: en
    # una escala de 0/1/2-3 proyectos, un "1" puede ser la posición o la
    # etiqueta, y son opciones distintas.
    "error_option_labels": (
        "Sin problema - no pude leer esa respuesta.\n\n"
        "Toca *{button}* en el mensaje anterior y selecciona de la lista, o "
        "escribe la opción tal como aparece.\n\n"
        "Soy un robot, así que solo entiendo las opciones."
    ),
    "confirm_p6": (
        "🧳 Anotado: *{slot}*.\n\n"
        "Recuerda empacar tus cosas y estar listo a tiempo para tu vuelo."
    ),
    "close_complete": (
        "🙏 Gracias por completar la encuesta.\n\n"
        "Hiciste parte de un experimento con dos versiones de la misma "
        "encuesta: una que pregunta de forma abierta y compleja, y otra que "
        "pregunta lo mismo en lenguaje sencillo con respuestas que se pueden "
        "tocar. Tú viste una de las dos.\n\n"
        "La segunda es la que recomendamos, y en la sesión mostraremos "
        "cómo se vieron las respuestas de tu grupo - y cuánto "
        "cambió el formato por sí solo.\n\n"
        "Nos vemos allá."
    ),
    "close_declined": (
        "Gracias por tu respuesta. Entendemos y respetamos que hayas decidido "
        "no participar en el ejercicio.\n\n"
        "Aun así, estás muy bienvenid@ a la sesión, donde "
        "hablaremos de cómo funciona la recolección de datos por "
        "WhatsApp y de sus riesgos."
    ),
    "close_incomplete": (
        "Gracias por las respuestas que alcanzaste a dar - quedaron "
        "registradas.\n\n"
        "Veremos cómo el formato de una pregunta cambia las respuestas "
        "que recibe, en vivo durante la sesión. Nos vemos allá."
    ),
    "unsolicited": (
        "👋 Gracias por tu mensaje.\n\n"
        "Este número solo se usa para una encuesta corta durante la "
        "sesión, así que no necesitas hacer nada más por aquí.\n\n"
        "Si tienes una pregunta, el equipo de IPA en la sesión puede "
        "ayudarte."
    ),
}

LANGS: dict[str, dict[str, Any]] = {"en": EN, "es": ES}


class BuildError(Exception):
    """Raised when a language table or a content SID is unusable."""


# ---------------------------------------------------------------------------
# Validation of the language tables themselves.
# ---------------------------------------------------------------------------


def check_language(lang: str) -> list[str]:
    """Return every way a language table would produce a broken message.

    Args:
        lang: Key into :data:`LANGS`.

    Returns:
        Human-readable problems. Empty means the table is usable.

    These are the limits Twilio and Studio impose rather than house style, and
    each one fails in a way that is hard to spot by reading: an over-long item
    title is rejected at template-create time with a generic error, and a comma
    in a label is not rejected at all - it just quietly produces a condition
    that can never match, which is precisely the "answer stored, respondent
    stranded" defect this repo now refuses to deploy.

    """
    table = LANGS[lang]
    problems: list[str] = []

    consent = table["consent"]
    for field_name in ("button_yes", "button_no"):
        title = consent[field_name]
        if len(title) > 25:
            problems.append(
                f"{lang}: consent {field_name} is {len(title)} chars, "
                f"quick-reply titles cap at 25: {title!r}"
            )
        if _has_emoji(title):
            problems.append(
                f"{lang}: consent {field_name} contains an emoji; button "
                f"labels are matched literally, keep them plain: {title!r}"
            )

    for key in QUESTION_KEYS:
        question = table["arm2"][key]
        kind = question_kind(lang, key)
        if kind not in QUESTION_KINDS:
            problems.append(
                f"{lang}: ARM2 {key} has kind {kind!r}, expected one of "
                f"{', '.join(QUESTION_KINDS)}"
            )
        if len(question["body"]) > 1024:
            problems.append(f"{lang}: ARM2 {key} body exceeds 1024 chars")

        # An integer question is a body and a regex. Everything below is about
        # an option list it does not have, and `options` is absent by design -
        # so anything that reads it has to stop here rather than default to an
        # empty list and quietly report "0 options".
        if kind == "integer":
            if "options" in question:
                problems.append(
                    f"{lang}: ARM2 {key} is an integer question and cannot have "
                    "options; the reply is validated by regex"
                )
            # Run the constraint rather than read it. A bound is a promise the
            # body makes to the respondent - "a number from 0 to 10" - and the
            # only way to know the split keeps it is to push the replies
            # through. A pattern that quietly refused "10" would strand every
            # respondent who answered at the top of the range.
            constraint = question.get("constraint", DEFAULT_CONSTRAINTS["integer"])
            for reply in question.get("accepts", []):
                if not evaluate_condition("regex", constraint, reply):
                    problems.append(
                        f"{lang}: ARM2 {key} refuses {reply!r}, which its own "
                        "body invites"
                    )
            for reply in question.get("refuses", []):
                if evaluate_condition("regex", constraint, reply):
                    problems.append(
                        f"{lang}: ARM2 {key} accepts {reply!r} as a valid "
                        "answer, so it would be stored as a real number"
                    )
            continue

        options = question["options"]
        cap = MAX_BUTTON_OPTIONS if kind == "button" else MAX_LIST_OPTIONS
        rendering = "quick-reply" if kind == "button" else "list-picker"
        if not 1 <= len(options) <= cap:
            problems.append(
                f"{lang}: ARM2 {key} has {len(options)} options, "
                f"{rendering} allows 1 to {cap}"
            )
        # A quick-reply title caps lower than a list-picker item, and its
        # description is never rendered - it only reaches the text fallback.
        item_cap = 25 if kind == "button" else 24
        seen: set[str] = set()
        for option in options:
            option_id, item, description = option[0], option[1], option[2]
            if len(item) > item_cap:
                problems.append(
                    f"{lang}: ARM2 {key} item is {len(item)} chars, cap is "
                    f"{item_cap} for {rendering}: {item!r}"
                )
            if kind != "button" and len(description) > 72:
                problems.append(
                    f"{lang}: ARM2 {key} description is {len(description)} "
                    f"chars, cap is 72: {description!r}"
                )
            if _has_emoji(item):
                problems.append(
                    f"{lang}: ARM2 {key} item contains an emoji; item labels "
                    f"are matched literally against the reply, keep them "
                    f"plain and put warmth in the body: {item!r}"
                )
            if item.casefold() in seen:
                problems.append(
                    f"{lang}: ARM2 {key} repeats the item label {item!r}, so "
                    "the stored code would be ambiguous"
                )
            seen.add(item.casefold())
            if len(option_id) > 200:
                problems.append(f"{lang}: ARM2 {key} id exceeds 200 chars")

    # Meta's limit on the button that opens a list. Twilio's documentation gives
    # no maximum for this field, so this is the platform limit rather than a
    # documented Twilio one - kept because being conservative here costs nothing.
    if len(table["arm2"]["button"]) > 20:
        problems.append(
            f"{lang}: list button {table['arm2']['button']!r} exceeds the "
            "20 characters WhatsApp shows on a list button"
        )

    problems.extend(_check_options_are_matchable(lang))
    problems.extend(_check_consent_is_matchable(lang))
    return problems


def _check_options_are_matchable(lang: str) -> list[str]:
    """Run every option through its own split condition and see where it lands.

    Reading a condition and believing it is how an unreachable option survives
    review. This executes it instead, using the same semantics Studio uses, and
    checks four things that have to hold: every label routes to the store
    widget, every position typed as a digit routes there too *where a digit is
    unambiguous*, a digit that is ambiguous is refused rather than guessed at,
    and something nobody would ever tap does not match.

    The last two matter as much as the first two. A pattern loose enough to
    match anything accepts junk as a real answer, which is worse than rejecting
    a real one - the respondent is never asked again and the row looks complete.
    And a pattern that accepts an ambiguous digit stores the wrong answer, which
    is worse still, because it is indistinguishable from a right one.
    """
    problems = []
    for key in QUESTION_KEYS:
        # An integer question has no options to be matchable. Its equivalent
        # guarantee is the constraint's own probe set in the spec module, which
        # pins that the pattern takes "12" and refuses "about 5".
        if question_kind(lang, key) == "integer":
            continue
        options = LANGS[lang]["arm2"][key]["options"]
        pattern = answer_pattern(options)
        ambiguous = positions_are_ambiguous(options)

        for index, option in enumerate(options, start=1):
            option_id, item = option[0], option[1]
            # option_id first: that is what a tapped list row actually sends.
            # Discovered the hard way - the first live test answered `p1_0`
            # where this expected "0 times", so every tap fell to the retry.
            must_accept = [option_id, item, f" {item} ", item.upper()]
            if not ambiguous:
                must_accept += [str(index), f"{index}."]
            for reply in must_accept:
                if not evaluate_condition("regex", pattern, reply):
                    problems.append(
                        f"{lang}: ARM2 {key} would not accept {reply!r}, so the "
                        f"option {item!r} is unreachable"
                    )

        # The collision itself. On a scale whose labels are numbers, position N
        # and label N mean different options, and accepting the bare digit picks
        # the wrong one silently.
        if ambiguous:
            for index in range(1, len(options) + 1):
                if evaluate_condition("regex", pattern, str(index)):
                    problems.append(
                        f"{lang}: ARM2 {key} has numeric option labels and still "
                        f"accepts the bare digit {index!r}. A respondent typing "
                        f"it means the label, not the position, and would be "
                        f"coded as the wrong option"
                    )

        for junk in ("banana", "", "0", str(len(options) + 1), "yes please"):
            if evaluate_condition("regex", pattern, junk):
                problems.append(
                    f"{lang}: ARM2 {key} accepts {junk!r} as a valid answer, so "
                    "junk would be stored as a real response"
                )

        # The split and the code mapping must agree. If the split is the more
        # tolerant of the two, a respondent is recorded as having answered
        # while their answer codes as `other` - which reads in the data as a
        # broken option rather than as the tolerance working.
        for index, option in enumerate(options, start=1):
            item = option[1]
            wanted = option_code(option, index)
            for reply in (item, str(index), f"{index}.", f"({index})", item.upper()):
                accepted = evaluate_condition("regex", pattern, reply)
                code = expected_code(options, reply)
                if accepted and code == "other":
                    problems.append(
                        f"{lang}: ARM2 {key} accepts {reply!r} but codes it as "
                        "'other'; the split is more tolerant than the mapping"
                    )
                if accepted and code not in ("other", wanted):
                    problems.append(
                        f"{lang}: ARM2 {key} codes {reply!r} as {code}, "
                        f"expected {wanted}"
                    )
    return problems


def _check_consent_is_matchable(lang: str) -> list[str]:
    """Check consent routes yes to yes, no to no, and nothing to both."""
    consent = LANGS[lang]["consent"]
    yes = word_pattern([consent["button_yes"]] + consent["typed_yes"].split("|"))
    no = word_pattern([consent["button_no"]] + consent["typed_no"].split("|"))

    problems = []
    for label, pattern, replies in (
        ("yes", yes, [consent["button_yes"], *consent["typed_yes"].split("|")]),
        ("no", no, [consent["button_no"], *consent["typed_no"].split("|")]),
    ):
        for reply in replies:
            if not evaluate_condition("regex", pattern, reply):
                problems.append(f"{lang}: consent {label} would not accept {reply!r}")

    # Consent is the one place an ambiguous match is unacceptable: a reply that
    # satisfies both branches would enrol someone by transition order.
    for reply in [
        consent["button_yes"],
        consent["button_no"],
        *consent["typed_yes"].split("|"),
        *consent["typed_no"].split("|"),
    ]:
        if evaluate_condition("regex", yes, reply) and evaluate_condition(
            "regex", no, reply
        ):
            problems.append(
                f"{lang}: consent reply {reply!r} matches both yes and no, so "
                "participation would be decided by transition order"
            )
    return problems


# ---------------------------------------------------------------------------
# Content template definitions.
#
# These are emitted rather than hand-written so the option text has exactly one
# home. None of them is ever submitted to Meta: quick-reply does not need
# approval in session and list-picker cannot be approved at all.
# ---------------------------------------------------------------------------


def consent_template_name(lang: str) -> str:
    """Friendly name of the consent quick-reply template for a language."""
    return f"data_use_demo_consent_{lang}"


def question_template_name(lang: str, key: str) -> str:
    """Friendly name of an ARM 2 list-picker template for a language."""
    return f"data_use_demo_arm2_{key.lower()}_{lang}"


def _generated_note(name: str, purpose: str) -> list[str]:
    return [
        "GENERATED by scripts/build_data_use_demo.py - do not hand-edit.",
        "Edit the language table in that script and re-run `just build-demo-flow`;",
        "the same strings drive this template, the flow's split conditions and the",
        "code stored for each answer, so editing here alone would desynchronise them.",
        "",
        purpose,
        "",
        "Create it with `just template-create` and never submit it to Meta.",
        f"Referenced by the flow as {name}.",
    ]


def consent_definition(lang: str) -> dict[str, Any]:
    """Build the consent quick-reply content template definition."""
    table = LANGS[lang]
    consent = table["consent"]
    name = consent_template_name(lang)
    return {
        "_comment": _generated_note(
            name,
            "Consent, as two quick-reply buttons. Sent inside the 24-hour window "
            "the opener's reply opens, so it needs no Meta approval. The buttons "
            "carry payload ids, but the flow splits on the message body so a "
            "typed reply works too.",
        ),
        "friendly_name": name,
        "language": table["language"],
        "types": {
            # A text fallback for any channel that cannot render buttons. The
            # numbers only appear here, in the fallback - the WhatsApp path has
            # no digits to type.
            "twilio/text": {
                "body": (
                    f"{consent['body']}\n\n"
                    f"1 - {consent['button_yes']}\n2 - {consent['button_no']}"
                )
            },
            "twilio/quick-reply": {
                "body": consent["body"],
                "actions": [
                    {"title": consent["button_yes"], "id": "consent_yes"},
                    {"title": consent["button_no"], "id": "consent_no"},
                ],
            },
        },
    }


def question_kind(lang: str, key: str) -> str:
    """How ARM 2 asks this question. Absent means the original list picker."""
    return LANGS[lang]["arm2"][key].get("kind", "list")


def option_keys(lang: str) -> tuple[str, ...]:
    """Return the ARM 2 questions that have an option list.

    Everything about matching a reply to an option - the pattern, the code
    mapping, the value labels - applies to these and not to the rest.
    """
    return tuple(key for key in QUESTION_KEYS if question_kind(lang, key) != "integer")


def templated_keys(lang: str) -> tuple[str, ...]:
    """Return the ARM 2 questions that need a content template.

    An ``integer`` question is a plain body and a regex, so it has no template
    to create and no SID to resolve. Asking for one anyway is how a build ends
    up looking up a friendly name that was never created.

    The same set as :func:`option_keys` today, and kept separate anyway: one is
    about what Twilio has to be told, the other about what a reply is checked
    against, and a future question kind could easily need one without the other.
    """
    return tuple(key for key in QUESTION_KEYS if question_kind(lang, key) != "integer")


def question_definition(lang: str, key: str) -> dict[str, Any]:
    """Build the ARM 2 content template definition for one question.

    A list picker or a set of quick-reply buttons, depending on the question's
    ``kind``. Both carry the same numbered ``twilio/text`` fallback, for a
    channel that cannot render either.
    """
    table = LANGS[lang]
    question = table["arm2"][key]
    kind = question_kind(lang, key)
    name = question_template_name(lang, key)
    options = question["options"]
    numbered = "\n".join(
        f"{index} - {option[1]}" for index, option in enumerate(options, start=1)
    )

    if kind == "button":
        return {
            "_comment": _generated_note(
                name,
                f"ARM 2 {key}, as {len(options)} quick-reply buttons. Sent "
                "inside the 24-hour window the opener's reply opens, so it "
                "needs no Meta approval. The buttons carry payload ids, but the "
                "flow splits on the message body so a typed reply works too.",
            ),
            "friendly_name": name,
            "language": table["language"],
            "types": {
                "twilio/text": {"body": f"{question['body']}\n\n{numbered}"},
                "twilio/quick-reply": {
                    "body": question["body"],
                    "actions": [
                        {"title": option[1], "id": option[0]} for option in options
                    ],
                },
            },
        }

    return {
        "_comment": _generated_note(
            name,
            f"ARM 2 {key}, as a tappable list of {len(options)} "
            "options. list-picker cannot be submitted for approval and cannot "
            "open a session; it only works inside the 24-hour window, which is "
            "why every ARM 2 question sits after the opener and consent.",
        ),
        "friendly_name": name,
        "language": table["language"],
        "types": {
            "twilio/text": {"body": f"{question['body']}\n\n{numbered}"},
            "twilio/list-picker": {
                "body": question["body"],
                "button": table["arm2"]["button"],
                "items": [
                    {"id": option[0], "item": option[1], "description": option[2]}
                    for option in question["options"]
                ],
            },
        },
    }


def template_definitions(lang: str) -> dict[str, dict[str, Any]]:
    """Every content template this language's flow needs, by friendly name."""
    definitions = {consent_template_name(lang): consent_definition(lang)}
    for key in templated_keys(lang):
        definitions[question_template_name(lang, key)] = question_definition(lang, key)
    return definitions


# ---------------------------------------------------------------------------
# Widget builders.
# ---------------------------------------------------------------------------


def send(name, body, next_state, *, x=0, y=0):
    """Build a one-way message widget."""
    return {
        "name": name,
        "type": "send-message",
        "properties": {"offset": {"x": x, "y": y}, "from": FROM, "body": body},
        "transitions": [
            {"event": "sent", "next": next_state},
            {"event": "failed", "next": next_state},
        ],
    }


def send_content(name, content_sid, *, x=0, y=0, variables=None):
    """Build a terminal one-way message that sends an approved template.

    Used for the closing message to somebody who never replied. They never
    opened the 24-hour window, so this send is business-initiated and a
    free-form body would fail with 63016 - a template is the only way to reach
    them at all.
    """
    properties = {
        "offset": {"x": x, "y": y},
        "from": FROM,
        "message_type": "content_template",
        "content_sid": content_sid,
    }
    if variables:
        properties["content_variables"] = variables
    return {
        "name": name,
        "type": "send-message",
        "properties": properties,
        "transitions": [{"event": "sent"}, {"event": "failed"}],
    }


def ask(name, body, on_reply, *, x=0, y=0, on_timeout="mark_no_reply"):
    """Build a free-text question. Wires reply, timeout and deliveryFailure."""
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": {
            "offset": {"x": x, "y": y},
            "from": FROM,
            "body": body,
            "timeout": TIMEOUT,
        },
        "transitions": [
            {"event": "incomingMessage", "next": on_reply},
            {"event": "timeout", "next": on_timeout},
            {"event": "deliveryFailure", "next": "mark_delivery_failed"},
        ],
    }


def ask_content(
    name, content_sid, on_reply, *, x=0, y=0, variables=None, on_timeout="mark_no_reply"
):
    """Build a question that sends a content template instead of a body.

    Used for the interactive messages - the consent buttons and the ARM 2
    lists. Studio needs `message_type` set as well as the SID, otherwise it
    treats the widget as a plain body and sends nothing.
    """
    properties = {
        "offset": {"x": x, "y": y},
        "from": FROM,
        "message_type": "content_template",
        "content_sid": content_sid,
        "timeout": TIMEOUT,
    }
    if variables:
        properties["content_variables"] = variables
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": properties,
        "transitions": [
            {"event": "incomingMessage", "next": on_reply},
            {"event": "timeout", "next": on_timeout},
            {"event": "deliveryFailure", "next": "mark_delivery_failed"},
        ],
    }


def set_vars(name, pairs, next_state, *, x=0, y=0):
    """Set flow variables."""
    return {
        "name": name,
        "type": "set-variables",
        "properties": {
            "offset": {"x": x, "y": y},
            "variables": [{"key": k, "value": v} for k, v in pairs],
        },
        "transitions": [{"event": "next", "next": next_state}],
    }


def split(name, tested, branches, default, *, x=0, y=0, condition="equal_to"):
    """Branch on a value. `default` is the mandatory noMatch destination.

    `branches` is a list of (value, destination) or (value, destination, label).
    For `matches_any_of`, value is a comma-delimited string of alternatives -
    see the comma warning in the language-table comment. The label is what the
    Studio console shows on the transition, so it is worth setting whenever the
    raw value is a long list: someone will read this canvas mid-session.
    """
    transitions = [{"event": "noMatch", "next": default}]
    for branch in branches:
        value, destination = branch[0], branch[1]
        label = branch[2] if len(branch) > 2 else f"is {value}"
        transitions.append(
            {
                "event": "match",
                "next": destination,
                "conditions": [
                    {
                        "friendly_name": label[:60],
                        "arguments": [tested],
                        "type": condition,
                        "value": value,
                    }
                ],
            }
        )
    return {
        "name": name,
        "type": "split-based-on",
        "properties": {"offset": {"x": x, "y": y}, "input": tested},
        "transitions": transitions,
    }


def arm_x(arm):
    """Lay ARM 1 and ARM 2 out side by side in the Studio canvas."""
    return -700 if arm == "ARM1" else 500


def error_body_for(table, options, kind: str = "list") -> str:
    """Pick the retry nudge that matches what this question can accept.

    The nudge is the one place the instrument tells a respondent how to answer,
    so it has to agree with the split *and* with what is on their screen.

    Two ways to get it wrong, both of which leave the respondent doing as they
    were told and still not being understood:

    * Inviting "just reply with the number" on a question whose labels are
      numbers asks for precisely the reply that :func:`positions_are_ambiguous`
      requires the split to refuse.
    * Telling somebody to tap the list button on a quick-reply question names a
      control that is not there - buttons have no list to open.
    """
    if kind == "button":
        return table["error_button"]
    key = "error_option_labels" if positions_are_ambiguous(options) else "error_option"
    return table[key].format(button=table["arm2"]["button"])


def stop_split(name, lang, on_continue, *, x, y):
    """Route a request to stop, before the reply is treated as an answer.

    Sits between every question and its store widget, in both arms. Without it
    a mid-survey "STOP" is stored as the answer to whatever was asked - in ARM 1
    verbatim, and in ARM 2 as a failed match that nudges twice and then asks the
    next question anyway. Being asked three more questions after saying stop is
    a research-ethics problem before it is a bug.

    Twilio's own opt-out handling covers the carrier keywords for SMS. Inside a
    WhatsApp session there is no such handling: it is an ordinary inbound
    message and nothing looks at it unless the flow does.
    """
    return split(
        f"stopcheck_{name}",
        f"{{{{widgets.{name}.inbound.Body}}}}",
        [(word_pattern(LANGS[lang]["stop_words"]), "mark_optout", "asked to stop")],
        on_continue,
        x=x,
        y=y,
        condition="regex",
    )


def slot_mapping(widget: str, options) -> str:
    """Build Liquid that resolves a reply to the option's own label.

    The twin of `code_mapping`, generated from the same options tuple in the
    same pass - which is the only reason it is safe to have a second mapping at
    all. `answers.py` is emphatic that a closed question is several artefacts
    that fail silently when they disagree; two generated from one source in one
    call cannot drift apart.

    It exists because the confirmation has to name the slot, and the raw reply
    is not the slot: somebody who types `4` instead of tapping would otherwise
    be told to be ready for their flight at 4.
    """
    removals = "".join(f' | replace: "{char}", ""' for char in _STRIPPED_PUNCTUATION)
    accept_positions = not positions_are_ambiguous(options)
    clauses = []
    for index, option in enumerate(options, start=1):
        alternatives = [
            f'"{normalise_reply(option[0])}"',
            f'"{normalise_reply(option[1])}"',
        ]
        if accept_positions:
            alternatives.append(f'"{index}"')
        clauses.append(f"{{% when {' or '.join(alternatives)} %}}{option[1]}")
    # The else branch cannot be reached from `store`, which only runs when the
    # split already matched. It is here because a Liquid case without one
    # renders empty, and an empty slot in the confirmation would read as a bug.
    return (
        f"{{% assign reply = widgets.{widget}.inbound.Body "
        f"| strip | downcase{removals} | strip %}}"
        f"{{% case reply %}}{''.join(clauses)}"
        "{% else %}the time you chose{% endcase %}"
    )


def open_question(arm, key, body, lang, *, y, next_state, confirm=None):
    """ARM 1: ask, accept whatever arrives, move on.

    No validation, deliberately. Validating an open answer would turn ARM 1
    into ARM 2 and destroy the comparison the demo exists to make. The one
    exception is a request to stop, which is not an answer to anything.
    """
    name = f"{arm}_{key}"
    x = arm_x(arm)
    after_store = f"confirm_{name}" if confirm else next_state
    states = [
        ask(name, body, f"stopcheck_{name}", x=x, y=y),
        stop_split(name, lang, f"store_{name}", x=x, y=y + 60),
        set_vars(
            f"store_{name}",
            [(f"{name}_status", "answered")],
            after_store,
            x=x,
            y=y + 140,
        ),
    ]
    if confirm:
        # ARM 1 can only repeat what was typed. That is the whole comparison,
        # arriving as a message the respondent reads rather than as a column
        # somebody discovers later.
        states.append(
            send(
                f"confirm_{name}",
                confirm.format(slot=f"{{{{widgets.{name}.inbound.Body}}}}"),
                next_state,
                x=x,
                y=y + 220,
            )
        )
    return states


def list_question(
    arm, key, content_sid, options, error_body, lang, *, y, next_state, confirm=None
):
    """ARM 2: send a list, accept a tap or a typed number, retry twice, move on.

    Eight widgets, mirroring the account's house pattern: ask, check for a
    request to stop, validate, store,
    count the retry, decide, nudge, give up. The retry limit matters ethically
    as much as technically - re-asking someone who cannot answer is badgering a
    volunteer.
    """
    name = f"{arm}_{key}"
    validate = f"split_{name}"
    retry = f"retry_{name}"
    give_up = f"giveup_{name}"
    error_widget = f"error_{name}"
    x = arm_x(arm)
    after_store = f"confirm_{name}" if confirm else next_state
    store_pairs = [
        (f"{name}_status", "answered"),
        (f"{name}_code", code_mapping(name, options)),
    ]
    if confirm:
        store_pairs.append((f"{name}_slot", slot_mapping(name, options)))

    states = [
        ask_content(name, content_sid, f"stopcheck_{name}", x=x, y=y),
        # Ahead of validation: a "STOP" is not a badly-formatted answer, and
        # letting it fall through would nudge them twice and then ask the next
        # question.
        stop_split(name, lang, validate, x=x, y=y + 40),
        split(
            validate,
            f"{{{{widgets.{name}.inbound.Body}}}}",
            [
                (
                    answer_pattern(options),
                    f"store_{name}",
                    f"tapped or typed one of {len(options)} options",
                )
            ],
            retry,
            x=x,
            y=y + 80,
            condition="regex",
        ),
        set_vars(
            f"store_{name}",
            store_pairs,
            after_store,
            x=x,
            y=y + 160,
        ),
        set_vars(
            retry,
            [
                (
                    f"tries_{name}",
                    f"{{% assign current = flow.variables.tries_{name} "
                    "| default: 0 %}{{ current | plus: 1 }}",
                )
            ],
            f"check_{name}",
            x=x + 260,
            y=y + 80,
        ),
        split(
            f"check_{name}",
            f"{{{{flow.variables.tries_{name}}}}}",
            [("1", error_widget), ("2", error_widget)],
            give_up,
            x=x + 260,
            y=y + 160,
        ),
        send(error_widget, error_body, name, x=x + 520, y=y + 160),
        set_vars(
            give_up,
            [(f"{name}_status", "multierror"), (f"{name}_code", "")],
            next_state,
            x=x + 260,
            y=y + 240,
        ),
    ]
    if confirm:
        # Only the answered path confirms. Somebody who gave up has nothing to
        # be told about, and telling them anyway would name a slot they never
        # picked.
        states.append(
            send(
                f"confirm_{name}",
                confirm.format(slot=f"{{{{flow.variables.{name}_slot}}}}"),
                next_state,
                x=x,
                y=y + 240,
            )
        )
    return states


def number_question(arm, key, body, constraint, error_body, lang, *, y, next_state):
    """ARM 2: ask for a number, refuse anything else, retry twice, move on.

    The same eight widgets as :func:`list_question` and the same retry limit -
    three differences, all of them about there being no option list:

    * the question is a plain body, not a content template, so there is no SID
      to resolve and nothing to create in the Content API;
    * the reply is validated against a regex rather than against labels; and
    * what gets stored is the reply itself, because there is no position to
      collapse it to.

    ``constraint`` is the regex a reply must match, taken from the question's
    own table entry so the bound is visible beside the wording that promises it.
    ARM 2 bounds every answer - a list to its rows, buttons to their titles, and
    a number to its range. "Any whole number" would not be a bound: it takes 900
    as readily as 2, and the arm's premise is that a reply lands in a known set.

    This is the arm's sharpest contrast on a numeric item: ARM 1 stores
    "about 3" and "two coffees" as answers, and ARM 2 cannot.
    """
    name = f"{arm}_{key}"
    validate = f"split_{name}"
    retry = f"retry_{name}"
    give_up = f"giveup_{name}"
    error_widget = f"error_{name}"
    x = arm_x(arm)

    return [
        ask(name, body, f"stopcheck_{name}", x=x, y=y),
        stop_split(name, lang, validate, x=x, y=y + 40),
        split(
            validate,
            f"{{{{widgets.{name}.inbound.Body}}}}",
            [(constraint, f"store_{name}", "replied with an in-range number")],
            retry,
            x=x,
            y=y + 80,
            condition="regex",
        ),
        # The reply, trimmed of the surrounding space the constraint tolerates.
        # No `_code`: there is no option table to map onto, and publishing an
        # empty one would put a column in the warehouse that means nothing.
        set_vars(
            f"store_{name}",
            [
                (f"{name}_status", "answered"),
                (
                    f"{name}_value",
                    f"{{{{widgets.{name}.inbound.Body | strip}}}}",
                ),
            ],
            next_state,
            x=x,
            y=y + 160,
        ),
        set_vars(
            retry,
            [
                (
                    f"tries_{name}",
                    f"{{% assign current = flow.variables.tries_{name} "
                    "| default: 0 %}{{ current | plus: 1 }}",
                )
            ],
            f"check_{name}",
            x=x + 260,
            y=y + 80,
        ),
        split(
            f"check_{name}",
            f"{{{{flow.variables.tries_{name}}}}}",
            [("1", error_widget), ("2", error_widget)],
            give_up,
            x=x + 260,
            y=y + 160,
        ),
        send(error_widget, error_body, name, x=x + 520, y=y + 160),
        set_vars(
            give_up,
            [(f"{name}_status", "multierror"), (f"{name}_value", "")],
            next_state,
            x=x + 260,
            y=y + 240,
        ),
    ]


# ---------------------------------------------------------------------------
# Flow assembly.
# ---------------------------------------------------------------------------


def build(
    lang: str, content_sids: dict[str, str], functions: dict[str, str]
) -> dict[str, Any]:
    """Assemble one language's flow definition.

    Args:
        lang: Key into :data:`LANGS`.
        content_sids: Friendly name to HX SID, for every template the flow
            references. Taking these as an argument rather than reading them
            from Twilio keeps this function pure and testable offline.
        functions: The deployed Functions coordinates, as
            :func:`resolve_functions` returns them. Also an argument, and for
            the same reason - plus a second one: hard-coding them is what made
            the built flow work on exactly one Twilio account.

    Returns:
        The flow definition, ready for `rtt flow deploy`.

    Raises:
        BuildError: If the language table is invalid or a SID is missing.

    """
    if lang not in LANGS:
        raise BuildError(f"unknown language {lang!r}; expected one of {sorted(LANGS)}")

    problems = check_language(lang)
    if problems:
        raise BuildError(
            f"{lang} language table is not usable:\n"
            + "\n".join(f"  {p}" for p in problems)
        )

    # Where the row is written, as it appears on the canvas. Defaulted rather
    # than required so a caller that only cares about the graph - every test
    # that builds a flow offline, and `resolve_functions` output from before
    # this key existed - keeps working unchanged.
    publish_widget = functions.get(
        "publish_widget", PUBLISH_TARGETS[DEFAULT_PUBLISH_TARGET]["widget"]
    )

    table = LANGS[lang]
    needed = [
        table["intro_template"],
        table["close_template"],
        consent_template_name(lang),
    ]
    needed += [question_template_name(lang, key) for key in templated_keys(lang)]
    missing = [name for name in needed if not content_sids.get(name)]
    if missing:
        raise BuildError("missing content SIDs for: " + ", ".join(missing))

    states: list[dict[str, Any]] = [
        {
            "name": "Trigger",
            "type": "trigger",
            "properties": {"offset": {"x": 0, "y": -1100}},
            "transitions": [
                # `rtt launch` creates executions over the REST API, which fires
                # incomingRequest - NOT incomingMessage. Leaving it unrouted
                # ends the execution at the trigger without sending anything,
                # and the launcher still reports it as "active".
                {"event": "incomingRequest", "next": "intro"},
                # Someone messaging the number cold does NOT get the survey.
                # In practice this path is almost entirely people being polite
                # after they finish - "thanks", "ok" - and re-sending the whole
                # opener to somebody who has just completed the survey is worse
                # than saying nothing. They also arrive with no preloaded data,
                # so they would produce a row with no caseid to join back to
                # the sampling frame, and the opener's name variable would be
                # empty, which fails the send outright (error 21656).
                #
                # They get one short acknowledgement and the execution ends. No
                # survey, no row: a dataset row should mean a sampled
                # respondent was asked something.
                {"event": "incomingMessage", "next": "unsolicited_reply"},
                {"event": "incomingCall"},
                {"event": "incomingConversationMessage"},
                {"event": "incomingParent"},
            ],
        },
        # The opening must be an approved template and must WAIT for a reply.
        # It is the only business-initiated message in the flow, so it is the
        # only one Meta reviews. Waiting means the respondent's reply opens the
        # 24-hour window, after which the buttons and lists below are free.
        send(
            "unsolicited_reply",
            table["unsolicited"],
            "unsolicited_reply",
            x=-600,
            y=-950,
        ),
        ask_content(
            "intro",
            content_sids[table["intro_template"]],
            "consent",
            x=0,
            y=-950,
            # A template variable that resolves to an empty string is rejected
            # outright - error 21656, "one or more variables resolve to null or an
            # empty string at send time" - and the entire opener fails to send. Two
            # ways that happens: somebody messages the number cold, so there is no
            # preloaded name at all, or a sample file has a blank cell. Both leave a
            # respondent who was contacted and then heard nothing. Seen live: a cold
            # inbound published a row reading `undeliverable` for exactly this.
            variables=[{"key": "1", "value": "{{flow.data.name | default: 'there'}}"}],
            # Someone who never answers the opener has never opened the 24-hour
            # window, so every later message to them is business-initiated and
            # fails with 63016. They get a published row and no closing message
            # - which is also the right thing to do rather than merely the only
            # possible one: there is nothing to thank a non-participant for.
            on_timeout="mark_never_started",
        ),
        ask_content(
            "consent",
            content_sids[consent_template_name(lang)],
            "split_consent",
            x=0,
            y=-820,
        ),
        # Tap or type, in either language's words. Nobody is enrolled by
        # ambiguity - but an unreadable reply is not a refusal either, and
        # routing it to `record_declined` made it one. "what is this?", a voice
        # note or an emoji would all have been published as an explicit
        # decline, and refusal rate is a headline number in a consent-based
        # study. Note the asymmetry that hid it: every ARM2 question gets two
        # retries, and the single most consequential question got none.
        split(
            "split_consent",
            "{{widgets.consent.inbound.Body}}",
            [
                (
                    word_pattern(
                        [table["consent"]["button_yes"]]
                        + table["consent"]["typed_yes"].split("|")
                    ),
                    "record_consent",
                    "consented",
                ),
                (
                    word_pattern(
                        [table["consent"]["button_no"]]
                        + table["consent"]["typed_no"].split("|")
                    ),
                    "record_declined",
                    "declined",
                ),
            ],
            "consent_unclear",
            x=0,
            y=-700,
            condition="regex",
        ),
        # One re-ask, then treat silence or a second unreadable reply as a
        # break-off rather than a decision. `consent_unclear` is its own value
        # so the two can be counted apart in the data.
        set_vars(
            "consent_unclear",
            [("set_consent", "unclear")],
            "consent_retry",
            x=-500,
            y=-700,
        ),
        ask_content(
            "consent_retry",
            content_sids[consent_template_name(lang)],
            "split_consent_retry",
            x=-500,
            y=-620,
            on_timeout="mark_no_reply",
        ),
        split(
            "split_consent_retry",
            "{{widgets.consent_retry.inbound.Body}}",
            [
                (
                    word_pattern(
                        [table["consent"]["button_yes"]]
                        + table["consent"]["typed_yes"].split("|")
                    ),
                    "record_consent",
                    "consented",
                ),
                (
                    word_pattern(
                        [table["consent"]["button_no"]]
                        + table["consent"]["typed_no"].split("|")
                    ),
                    "record_declined",
                    "declined",
                ),
            ],
            # Still unreadable. Not enrolled, and not recorded as a refusal
            # either - `set_consent` stays `unclear` and the outcome is a
            # break-off, which is what it actually was.
            "mark_no_reply",
            x=-500,
            y=-540,
            condition="regex",
        ),
        set_vars("record_consent", [("set_consent", "yes")], "split_arm", x=0, y=-580),
        set_vars(
            "record_declined",
            [("set_consent", "no"), ("outcome", "declined")],
            "finish",
            x=400,
            y=-580,
        ),
        # The arm is preloaded, not drawn here: randomisation happens offline in
        # the sample file, so it is reproducible and balance can be checked
        # before anyone is contacted.
        #
        # noMatch goes to a marker widget rather than straight to ARM1. A
        # missing or misspelled `arm` column would otherwise send *everyone*
        # down ARM1 - the flow runs, every row publishes, `arm` is blank, and
        # the experiment has quietly become a single-condition survey. The
        # marker makes it one visible value in the data instead.
        split(
            "split_arm",
            "{{flow.data.arm}}",
            [("1", "ARM1_P1"), ("2", "ARM2_P1")],
            "mark_arm_missing",
            x=0,
            y=-460,
        ),
        set_vars(
            "mark_arm_missing",
            [("set_arm_missing", "1")],
            "ARM1_P1",
            x=-400,
            y=-400,
        ),
    ]

    for index, key in enumerate(QUESTION_KEYS):
        following = (
            f"ARM1_{QUESTION_KEYS[index + 1]}"
            if index + 1 < len(QUESTION_KEYS)
            else "mark_complete"
        )
        states.extend(
            open_question(
                "ARM1",
                key,
                table["arm1"][key],
                lang,
                y=-300 + index * 340,
                next_state=following,
                confirm=table.get("confirm_p6") if key == "P6" else None,
            )
        )

    for index, key in enumerate(QUESTION_KEYS):
        following = (
            f"ARM2_{QUESTION_KEYS[index + 1]}"
            if index + 1 < len(QUESTION_KEYS)
            else "mark_complete"
        )
        question = table["arm2"][key]
        if question_kind(lang, key) == "integer":
            states.extend(
                number_question(
                    "ARM2",
                    key,
                    question["body"],
                    question.get("constraint", DEFAULT_CONSTRAINTS["integer"]),
                    table["error_numeric"],
                    lang,
                    y=-300 + index * 340,
                    next_state=following,
                )
            )
            continue

        # A list picker and a set of quick-reply buttons are the same eight
        # widgets; only the content template behind the SID differs.
        states.extend(
            list_question(
                "ARM2",
                key,
                content_sids[question_template_name(lang, key)],
                question["options"],
                # Named from the same entry the list picker uses, so the nudge
                # cannot end up telling someone to tap a button that is not
                # there. And on a question whose labels are numbers, the
                # variant that does *not* invite a bare digit - inviting one
                # there asks for exactly the reply the split has to refuse.
                error_body_for(table, question["options"], question_kind(lang, key)),
                lang,
                y=-300 + index * 340,
                next_state=following,
                confirm=table.get("confirm_p6") if key == "P6" else None,
            )
        )

    states.extend(
        [
            set_vars(
                "mark_complete",
                [("set_complete", "1"), ("outcome", "complete")],
                "finish",
                x=0,
                y=1200,
            ),
            set_vars(
                "mark_no_reply",
                [("set_no_reply", "1"), ("outcome", "incomplete")],
                "finish",
                x=900,
                y=1200,
            ),
            # Asked to stop. Their answers so far are kept - they were given
            # freely, and discarding them would be its own kind of disrespect -
            # but nothing further is asked, and the outcome says why the rest
            # is blank so it is not read as a break-off.
            set_vars(
                "mark_optout",
                [("set_optout", "1"), ("outcome", "optout")],
                "finish",
                x=1300,
                y=1080,
            ),
            # Never answered the opener: the window never opened, so no closing
            # message is possible. The row is still published.
            set_vars(
                "mark_never_started",
                [("set_no_reply", "1"), ("outcome", "unreachable")],
                "finish",
                x=1700,
                y=1200,
            ),
            # The message did not arrive. Sending another one would fail the
            # same way, so this path gets no closing message either.
            set_vars(
                "mark_delivery_failed",
                [("set_fail", "1"), ("outcome", "undeliverable")],
                "finish",
                x=1300,
                y=1200,
            ),
            # Single convergence point. Every terminal path arrives here, so a
            # row is published whatever happened - complete, declined, timed
            # out or undeliverable.
            # Pure convergence point now that times come from UTC sources.
            # Kept as its own widget because every terminal path routes through
            # it, which is what guarantees a row exists whatever happened.
            set_vars(
                "finish",
                # `enc_status` is set optimistically here and overridden on the
                # encryption widget's failure branch. Setting it at the
                # convergence point means every published row carries the
                # column, so a blank identifier can be read as "this respondent
                # had no name" rather than "encryption failed and nobody knew".
                [
                    ("set_reached_finish", "1"),
                    ("enc_status", "ok"),
                    # `outcome` says which of the six terminal paths ran.
                    # `final_status` is the four-value rollup an analysis groups
                    # by, derived here because this is the one widget every path
                    # passes through. Six widgets each setting it correctly is
                    # six chances to add a seventh that forgets; deriving it at
                    # the convergence point cannot be forgotten.
                    #
                    # Generated from FINAL_STATUS_BY_OUTCOME, so the flow and
                    # `final_status_for` cannot disagree about what a `declined`
                    # means. Overridden to `failed` below if encryption fails,
                    # which is the one thing not known yet at this point.
                    ("final_status", final_status_liquid()),
                ],
                "function_encrypt",
                x=0,
                y=1320,
            ),
            {
                "name": "function_encrypt",
                "type": "run-function",
                "properties": {
                    "offset": {"x": 0, "y": 1440},
                    "service_sid": functions["service_sid"],
                    "environment_sid": functions["environment_sid"],
                    "function_sid": functions["encrypt_sid"],
                    "url": functions["encrypt_url"],
                    "parameters": [
                        {"key": "enc_name", "value": "{{flow.data.name}}"},
                        {
                            "key": "enc_p_number_original",
                            "value": "{{contact.channel.address}}",
                        },
                    ],
                },
                # A failed encryption still publishes - a row with no identifiers
                # is far better than no row - but it goes via a widget that says
                # so. Publishing straight from the failure branch writes
                # `enc_name=""` under `outcome=complete`, which is
                # indistinguishable from a respondent who had no name in the
                # sample file, and nothing anywhere records that the identifiers
                # were lost.
                "transitions": [
                    {"event": "success", "next": publish_widget},
                    {"event": "fail", "next": "mark_encrypt_failed"},
                ],
            },
            set_vars(
                "mark_encrypt_failed",
                # The row is still published - losing the answers because the
                # PII could not be sealed would be the worse trade - but it must
                # not be counted as a clean completion. This is the only place
                # `final_status` is overridden after `finish` derives it, because
                # it is the only failure that happens *after* the outcome is
                # already known.
                [
                    ("enc_status", "encrypt_failed"),
                    ("final_status", ENCRYPTION_FAILED_STATUS),
                ],
                publish_widget,
                x=-500,
                y=1500,
            ),
        ]
    )

    published: list[tuple[str, str]] = [
        ("caseid", "{{flow.data.caseid}}"),
        ("lang", lang),
        ("arm", "{{flow.data.arm}}"),
        ("enc_name", "{{widgets.function_encrypt.parsed.enc_name}}"),
        (
            "enc_p_number_original",
            "{{widgets.function_encrypt.parsed.enc_p_number_original}}",
        ),
        # Whether the two columns above mean anything. `ok` or `encrypt_failed`.
        ("enc_status", "{{flow.variables.enc_status}}"),
        ("set_consent", "{{flow.variables.set_consent}}"),
        # Set only when `arm` was missing from the sample file. Without it, a
        # misspelled column silently routes everyone to ARM1 and the two-arm
        # comparison becomes a one-arm survey that nothing reports.
        ("set_arm_missing", "{{flow.variables.set_arm_missing}}"),
        ("set_complete", "{{flow.variables.set_complete}}"),
        ("set_no_reply", "{{flow.variables.set_no_reply}}"),
        ("set_fail", "{{flow.variables.set_fail}}"),
        ("outcome", "{{flow.variables.outcome}}"),
        # The rollup an analysis groups by, beside the path it came from.
        ("final_status", "{{flow.variables.final_status}}"),
        # The join key back to the execution log. Without it a warehouse row
        # cannot be traced to the execution that produced it.
        ("execution_sid", "{{flow.sid}}"),
        # The two timestamps that bracket a respondent's participation, both
        # UTC. `sent_at` is supplied by the launcher at the moment of contact,
        # because Studio cannot produce a UTC time itself; `submitted_at` is
        # stamped server-side by the publish Function when the row is written,
        # which is the last step of the flow. Their difference is the time the
        # respondent took, and both mean what they say.
        ("sent_at", "{{flow.data.sent_at}}"),
    ]
    for arm in ("ARM1", "ARM2"):
        for key in QUESTION_KEYS:
            name = f"{arm}_{key}"
            # Raw reply, then its status, then - for the list arm only - the
            # normalised option number. Order matters: the status column has to
            # sit beside its answer for the unpaired-answers check to read the
            # pairing, and for an analyst to read it in the warehouse.
            published.append((name, f"{{{{widgets.{name}.inbound.Body}}}}"))
            published.append(
                (f"{name}_status", f"{{{{flow.variables.{name}_status}}}}")
            )
            if arm == "ARM2":
                # An integer question has no option table, so there is no
                # position to normalise to. It publishes the validated number
                # instead - a different column name on purpose, so nobody reads
                # a free number as though it were an option code.
                suffix = "value" if question_kind(lang, key) == "integer" else "code"
                published.append(
                    (
                        f"{name}_{suffix}",
                        f"{{{{flow.variables.{name}_{suffix}}}}}",
                    )
                )

    states.append(
        {
            "name": publish_widget,
            "type": "run-function",
            "properties": {
                "offset": {"x": 0, "y": 1560},
                "service_sid": functions["service_sid"],
                "environment_sid": functions["environment_sid"],
                "function_sid": functions["publish_sid"],
                "url": functions["publish_url"],
                "parameters": [{"key": k, "value": v} for k, v in published],
            },
            # The publish step reports its own failure correctly, so the flow
            # must not throw that away. Routing `fail` to the closing message
            # thanks a respondent whose row does not exist and never will -
            # success from every angle at the moment of failure, and invisible
            # until someone counts the rows months later.
            #
            # There is nowhere to persist the flag except the execution context,
            # because the thing that persists rows is what just failed. That is
            # enough: `rtt fetch` reads the context, and the respondent is not
            # told the survey landed when it did not.
            "transitions": [
                {"event": "success", "next": "split_closing"},
                {"event": "fail", "next": "record_publish_failure"},
            ],
        }
    )

    states.append(
        {
            "name": "record_publish_failure",
            "type": "set-variables",
            "properties": {
                "offset": {"x": -1000, "y": 1680},
                # The flag only. Overwriting `outcome` would destroy the one
                # thing still worth having: no row was written, so the execution
                # context is the only surviving record of whether this
                # respondent completed, declined or timed out - and `rtt fetch`
                # reads exactly that. `set_publish_failed` already says what
                # went wrong without erasing what happened.
                "variables": [{"key": "set_publish_failed", "value": "1"}],
            },
            "transitions": [{"event": "next"}],
        }
    )

    states.extend(
        [
            # Four of the five outcomes get a closing message; they differ only
            # in which mechanism can reach the respondent.
            #
            #   complete / declined / incomplete   replied within the last hour,
            #                                      so the window is open and the
            #                                      body can be free-form and as
            #                                      long as the outcome needs
            #   unreachable                        never replied, so the window
            #                                      never opened - only an
            #                                      approved template gets there
            #   undeliverable                      the first message never
            #                                      arrived, so neither will this
            #                                      one. Nothing is sent
            split(
                "split_closing",
                "{{flow.variables.outcome}}",
                [
                    ("complete", "close_complete"),
                    ("declined", "close_declined"),
                    ("incomplete", "close_incomplete"),
                    ("unreachable", "close_never_started"),
                    ("undeliverable", "end_without_message"),
                    # One acknowledgement, so they know it worked. Saying stop
                    # and then hearing nothing is indistinguishable from saying
                    # stop and not being heard.
                    ("optout", "close_optout"),
                ],
                "end_without_message",
                x=0,
                y=1680,
            ),
            send_content(
                "close_never_started",
                content_sids[table["close_template"]],
                x=900,
                y=1800,
                variables=[
                    {"key": "1", "value": "{{flow.data.name | default: 'there'}}"}
                ],
            ),
            {
                "name": "end_without_message",
                "type": "set-variables",
                "properties": {
                    "offset": {"x": 1400, "y": 1800},
                    "variables": [{"key": "set_closed_silently", "value": "1"}],
                },
                "transitions": [{"event": "next"}],
            },
            send(
                "close_complete",
                table["close_complete"],
                "close_complete",
                x=-400,
                y=1800,
            ),
            send(
                "close_declined", table["close_declined"], "close_declined", x=0, y=1800
            ),
            send(
                "close_incomplete",
                table["close_incomplete"],
                "close_incomplete",
                x=400,
                y=1800,
            ),
            send(
                "close_optout",
                table["stop_ack"],
                "close_optout",
                x=-900,
                y=1800,
            ),
        ]
    )

    # The closing messages and the unsolicited acknowledgement are terminal:
    # drop their outgoing transitions so the flow ends rather than looping back
    # into itself.
    for state in states:
        if state["name"].startswith("close_") or state["name"] == "unsolicited_reply":
            state["transitions"] = [{"event": "sent"}, {"event": "failed"}]

    return {
        "description": table["description"],
        "states": states,
        "initial_state": "Trigger",
        "flags": {"allow_concurrent_calls": True},
    }


def codebook_rows(lang: str) -> list[dict[str, str]]:
    """Return the value labels for this language's coded answers.

    The published row carries a code, not a label. That is deliberate - a label
    in every row is redundant, and it would be in whichever language that
    respondent happened to receive, which makes pooling the two impossible.

    What the data needs instead is value labels, the same thing a SurveyCTO
    choice list or a Stata `label define` provides. Generating them from the
    option tables that also build the flow means the labels cannot drift from
    the codes they describe: change an option and the codebook changes with it,
    in the same commit.

    `option_id` is included because it is what a tapped list row actually sends
    and therefore what lands in the raw answer column, so it is the join key
    between a raw reply and its meaning.
    """
    table = LANGS[lang]
    rows: list[dict[str, str]] = []

    # `final_status` is a coded column like any other and needs its labels here,
    # or the one column an analysis groups by is the one with no codebook entry.
    for status in FINAL_STATUSES:
        rows.append(
            {
                "lang": lang,
                "arm": "",
                "question": "final_status",
                "variable": "final_status",
                "code": status,
                "option_id": "",
                "label": status,
                "description": FINAL_STATUS_NOTES[status],
            }
        )

    for key in QUESTION_KEYS:
        question = table["arm2"][key]
        # An integer question has no value labels: the column holds the number
        # the respondent typed, which means itself. One row saying so, because a
        # codebook silently missing a published column reads as an oversight.
        if question_kind(lang, key) == "integer":
            accepts = question.get("accepts")
            bound = (
                f"{min(accepts, key=int)}-{max(accepts, key=int)}"
                if accepts
                else "any whole number"
            )
            rows.append(
                {
                    "lang": lang,
                    "arm": "2",
                    "question": f"ARM2_{key}",
                    "variable": f"ARM2_{key}_value",
                    "code": "",
                    "option_id": "",
                    "label": f"Number in {bound}",
                    # The range is the value label. Without it an analyst cannot
                    # tell a missing answer from one the split refused, and both
                    # arrive as a blank cell.
                    "description": (
                        f"Validated number, {bound}. Blank means refused or "
                        "unanswered - read the status column"
                    ),
                }
            )
            continue
        for index, option in enumerate(question["options"], start=1):
            rows.append(
                {
                    "lang": lang,
                    "arm": "2",
                    "question": f"ARM2_{key}",
                    "variable": f"ARM2_{key}_code",
                    "code": option_code(option, index),
                    "option_id": option[0],
                    "label": option[1],
                    "description": option[2],
                }
            )
    return rows


def write_codebook(lang: str) -> Path:
    """Write the value labels for a language as CSV."""
    path = CODEBOOK_DIR / f"data_use_demo_{LANGS[lang]['flow_suffix']}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = codebook_rows(lang)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def flow_path(lang: str) -> Path:
    """Where a language's flow definition is written."""
    return FLOW_DIR / f"data_use_demo_{LANGS[lang]['flow_suffix']}.json"


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def write_template_definitions(lang: str) -> list[Path]:
    """Write the generated content template definitions for a language."""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name, definition in template_definitions(lang).items():
        path = TEMPLATE_DIR / f"{name}.json"
        path.write_text(
            json.dumps(definition, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def resolve_functions(client, target: str = DEFAULT_PUBLISH_TARGET) -> dict[str, str]:
    """Look up the deployed Functions service, environment, and both functions.

    Args:
        client: An authenticated Twilio client.
        target: Which publish destination to wire up, a key of
            :data:`PUBLISH_TARGETS`.

    Returns:
        The keys a ``run-function`` widget needs: ``service_sid``,
        ``environment_sid``, ``encrypt_sid``, ``publish_sid``, ``encrypt_url``
        and ``publish_url``, plus ``publish_widget`` - the name the publish step
        carries on the canvas.

    Raises:
        BuildError: If the service, its environment, or either function is
            missing - which means `just deploy-functions` has not been run on
            this account.

    Studio needs the deployed **url** as well as the three SIDs; validation
    rejects a run-function widget without it, and the domain carries a random
    per-account suffix, so it has to be read from the environment rather than
    constructed.

    """
    if target not in PUBLISH_TARGETS:
        raise BuildError(
            f"Unknown publish target {target!r}. Choose one of "
            f"{', '.join(sorted(PUBLISH_TARGETS))}."
        )
    publish = PUBLISH_TARGETS[target]

    # No `limit=`: the SDK turns it into a page_size, Serverless caps that at
    # 100, and asking for more is a 400 rather than a truncation. Bare .list()
    # walks the pages, so an account with many services still resolves.
    service = next(
        (
            s
            for s in client.serverless.v1.services.list()
            if s.unique_name == FUNCTIONS_SERVICE_NAME
        ),
        None,
    )
    if service is None:
        raise BuildError(
            f"No Functions service named {FUNCTIONS_SERVICE_NAME!r} on this "
            f"account. Run `just deploy-functions` first - it creates the "
            f"service and deploys encrypt_fields and every publish target."
        )

    environments = client.serverless.v1.services(service.sid).environments.list()
    environment = next(iter(environments), None)
    if environment is None:
        raise BuildError(
            f"The {FUNCTIONS_SERVICE_NAME!r} service has no environment. "
            f"Re-run `just deploy-functions`."
        )

    functions = {
        f.friendly_name: f.sid
        for f in client.serverless.v1.services(service.sid).functions.list()
    }
    missing = [
        name
        for name in (ENCRYPT_FUNCTION_NAME, publish["function"])
        if name not in functions
    ]
    if missing:
        # Naming the target matters here. A service deployed before this repo
        # grew a second destination has encrypt_fields and publish_motherduck
        # and nothing else, so `--publish-target gsheets` fails on an account
        # that is otherwise perfectly set up - and "re-run deploy-functions"
        # alone does not explain why.
        raise BuildError(
            f"Deployed but incomplete: {FUNCTIONS_SERVICE_NAME!r} is missing "
            f"{', '.join(missing)}, which the {target!r} publish target needs. "
            f"Re-run `just deploy-functions`."
        )

    host = environment.domain_name
    return {
        "service_sid": service.sid,
        "environment_sid": environment.sid,
        "encrypt_sid": functions[ENCRYPT_FUNCTION_NAME],
        "publish_sid": functions[publish["function"]],
        "encrypt_url": f"https://{host}{ENCRYPT_PATH}",
        "publish_url": f"https://{host}{publish['path']}",
        "publish_widget": publish["widget"],
    }


def resolve_sids(lang: str) -> tuple[dict[str, str], list[str]]:
    """Look up the flow's content templates on the account by friendly name.

    Returns:
        The SIDs found, and the friendly names that do not exist yet.

    Resolving by name rather than pasting HX SIDs into this file means a
    recreated template is picked up on the next build instead of leaving a flow
    pointing at something that no longer exists.

    """
    cfg.load_env()
    conf = cfg.TwilioConfig.from_env()
    client = Client(conf.account_sid, conf.auth_token)

    table = LANGS[lang]
    wanted = [
        table["intro_template"],
        table["close_template"],
        consent_template_name(lang),
    ]
    wanted += [question_template_name(lang, key) for key in templated_keys(lang)]

    found: dict[str, str] = {}
    missing: list[str] = []
    stale: list[str] = []
    for name in wanted:
        existing = tpl.find_by_name(client, name)
        if existing is None:
            missing.append(name)
            continue
        found[name] = existing.sid
        # The template exists, but does it still say what this file says? The
        # flow references it by SID, so drift here is invisible to the flow
        # check, to the linter and to the tests - and reaches the respondent.
        path = TEMPLATE_DIR / f"{name}.json"
        if path.exists():
            try:
                if tpl.drifted_types(existing, tpl.load_definition(path)):
                    stale.append(name)
            except tpl.TemplateError:
                # A definition this build did not write. Not ours to judge.
                pass
    return found, missing, stale


def build_one(lang: str, target: str = DEFAULT_PUBLISH_TARGET) -> bool:
    """Emit one language's templates and flow. Returns True on success."""
    print(f"\n=== {LANGS[lang]['name']} ({lang}) ===")

    problems = check_language(lang)
    if problems:
        print("  language table is not usable:")
        for problem in problems:
            print(f"    {problem}")
        return False

    for path in write_template_definitions(lang):
        print(f"  template  {path.relative_to(REPO_ROOT).as_posix()}")

    book = write_codebook(lang)
    print(f"  codebook  {book.relative_to(REPO_ROOT).as_posix()}")

    try:
        cfg.load_env()
        conf = cfg.TwilioConfig.from_env()
        functions = resolve_functions(Client(conf.account_sid, conf.auth_token), target)
    except BuildError as exc:
        print(f"\n  {exc}")
        return False
    print(
        f"  functions {functions['service_sid']} "
        f"({functions['encrypt_url'].split('//')[1].split('/')[0]})"
    )
    # Printed on every build, not only when it is unusual. Which destination a
    # flow writes to is invisible in the deploy output and expensive to discover
    # afterwards - it is one widget name buried in an 80-widget definition.
    print(f"  publish   {target} -> {functions['publish_widget']}")

    found, missing, stale = resolve_sids(lang)
    if stale:
        print("\n  Cannot build the flow - these templates exist on the account")
        print("  but no longer say what this repo says. The flow references them")
        print("  by SID, so nothing downstream would notice: the flow check, the")
        print("  linter and the tests would all pass while respondents read the")
        print("  old wording.")
        for name in stale:
            print(f"    {name}")
        print("\n  Make Twilio match the repo, then re-run this build:")
        print("    just demo-templates-sync")
        return False
    if missing:
        print("\n  Cannot build the flow yet - these content templates do not")
        print("  exist on this account. Create them, then re-run this build:")
        for name in missing:
            if name in (LANGS[lang]["intro_template"], LANGS[lang]["close_template"]):
                where = f"templates/{name}.json"
                note = "  # bookend: needs Meta approval before a real round"
            else:
                where = f"templates/generated/{name}.json"
                note = "  # in-session only: never submit this one"
            print(f"    just template-create {where}{note}")
        return False

    definition = build(lang, found, functions)
    path = flow_path(lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(definition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    questions = sum(
        1 for s in definition["states"] if s["type"] == "send-and-wait-for-reply"
    )
    print(f"  flow      {path.relative_to(REPO_ROOT).as_posix()}")
    print(f"            {len(definition['states'])} widgets, {questions} questions")

    findings = check_flow(definition)
    errors = [f for f in findings if f.severity == "error"]
    if not findings:
        print("  check     all checks passed")
    for finding in findings:
        print(f"  check     [{finding.severity}] {finding.code}  {finding.summary}")
        for line in finding.detail[:6]:
            print(f"                {line}")
    return not errors


def main() -> None:
    """Build one or both languages."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--lang",
        choices=[*sorted(LANGS), "both"],
        default="both",
        help="Language to build. Defaults to both, which is the point.",
    )
    parser.add_argument(
        "--publish-target",
        choices=sorted(PUBLISH_TARGETS),
        default=DEFAULT_PUBLISH_TARGET,
        help=(
            "Where a submission is written. 'motherduck' inserts over the "
            "Postgres wire protocol; 'gsheets' appends a row to a spreadsheet, "
            "which needs only a sheet and a service account. Defaults to "
            f"{DEFAULT_PUBLISH_TARGET}."
        ),
    )
    args = parser.parse_args()

    languages = sorted(LANGS) if args.lang == "both" else [args.lang]
    ok = [build_one(lang, args.publish_target) for lang in languages]

    if not all(ok):
        print("\nNot every language built cleanly - see above.")
        raise SystemExit(1)

    print("\nBoth languages built from one structure, so a fix to the graph")
    print("lands in both. Deploy with:")
    for lang in languages:
        rel = flow_path(lang).relative_to(REPO_ROOT).as_posix()
        print(f"  just flow-deploy {rel}")


if __name__ == "__main__":
    main()
