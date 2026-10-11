# Writing a survey as a spreadsheet

You describe the instrument as rows, the way you would an XLSForm. The toolkit
turns those rows into a Studio flow.

```text
   survey builder  ⇄ ┐
                     ├   my_survey.json   →   flows/my_survey.json   →   Studio
   my_survey.xlsx  ⇄ ┘   (git carries this)   (just survey build)        (just flow deploy)
```

Edit the JSON in either surface: the **survey builder**, a visual editor with a
card per step, or the **workbook** in Excel. Both read and write the same file,
so a team can mix them.

`just survey build` compiles a spec that passes `just survey check` into a
Studio flow, and `just flow deploy` puts it on your account as a draft. The demo
instrument is still built by `scripts/build_data_use_demo.py`, from its own
tables; it shares its widgets with the compiler.

One row is one question **including the whole subgraph it becomes**. A closed
question with retries is eight Studio widgets (ask, note it went quiet,
validate, store, give up, count the retry, decide, nudge), and the no-reply
reminder adds about eight more. A plain text question is three. The `widgets`
column in the workbook gives the exact count next to each row, so the cost of a
design choice is visible while you are making it.

That is the whole point. The demo instrument is 97 widgets for six questions
asked two ways, and nobody reviews a canvas that size — which is the real reason
instruments go to field with defects in them.

## This is for surveys

The spine wrapped around your rows is a survey's spine, and it assumes four
things:

- **Consent is asked, once, before any question.** `rtt survey check` refuses an
  instrument that asks questions without it.
- **Every terminal path publishes exactly one row per respondent** — complete,
  declined, timed out, undeliverable. That is what makes a dataset countable.
- **Closings are chosen by survey outcome**, from a fixed vocabulary.
- **One execution is one respondent answering once.**

**Reminders, multi-wave interventions and notifications break those.** A reminder
campaign sends three messages over a week, collects no answer, and probably wants
one row per *send* rather than per person. A multi-wave intervention took consent
at enrolment, not in the message, and contacts the same person repeatedly by
design. A notification flow may publish nothing, because there is no
respondent-supplied data to publish.

Those are not lesser flows — they are a different shape, and this format would
either fight them or quietly misdescribe them. Build them as Studio flows and
check them with `rtt flow check`, which judges a graph on its own terms. Reach
for the spec when the thing you are building asks a sampled person questions and
expects a dataset out.

## Start from the sample, not from an existing survey

```powershell
just survey new --output my_survey.xlsx
```

`sample_template.xlsx` in the repository root is the same thing, committed so you
can look at it without installing anything. It is a small working instrument, not
a blank sheet, and `rtt survey check` passes it as-is — so the first finding you
ever see is about something you changed.

Do not start by copying another round's survey. A family of copied flows can share
one identical break-off defect because they were cloned from each other, and six
of them are published.

Open the **help-survey** sheet first. It documents every column and it lives
inside the file, so it is there when you need it rather than in a wiki.

## The four sheets

| sheet | one row is |
| --- | --- |
| `survey` | a question, or a positioned message |
| `choices` | an answer option |
| `messages` | a string with no position — a nudge, the stop words, the reminder |
| `settings` | the instrument as a whole |

### `survey`

| column | what it does |
| --- | --- |
| `type` | how the question is asked, and therefore what it becomes |
| `name` | the variable name, and the warehouse column |
| `label:<lang>` | the message the respondent receives |
| `role` | **blank** for an ordinary question; a spine position otherwise |
| `relevance` | when the row applies, e.g. `${arm}='2'`, `${Q2}>0 and ${arm}!='1'` (see below) |
| `retries` | how many times to re-ask a reply that does not validate |
| `constraint` | the regex a reply must match, for `integer` / `decimal` |
| `constraint_message` | which nudge to send when it does not |
| `minimum` / `maximum` | the smallest and largest number an `integer` / `decimal` takes, both included |
| `timeout` | seconds to wait for a reply (after the reminder, when there is one) |
| `timeout_reminder` | default yes: send the no-reply reminder with the question again |
| `timeout_message` | this question's own reminder text, a key in `messages`; blank uses `timeout_nudge` |
| `publish` / `encrypt` | whether the answer reaches the warehouse, and whether encrypted first |

The types, and what each becomes:

| `type` | the respondent sees | widgets, with retries and no reminder |
| --- | --- | --- |
| `text` | a plain message; **any** reply is stored | 3 |
| `integer` / `decimal` | a plain message; the reply must match `constraint` | 8, or 9 with a `minimum` / `maximum` |
| `select_list <list>` | a tappable list, up to 10 options | 8 |
| `select_button <list>` | up to 3 tappable buttons | 8 |
| `template` | an approved message, and waits for a reply | 1, or 2 when it listens for a stop word |
| `note` | a message expecting no reply | 1 |
| `begin group` / `end group` | a branch; the condition goes in `relevance` | 1 per comparison |

The no-reply reminder adds about eight to a question, and listening for a stop
word one more.

`text` and `integer` look identical to a respondent and differ entirely in the
data. `text` stores whatever arrives, including `about 5`. Use `integer` whenever
the answer is a number — the reply is checked before it is stored, and an
unparsable one is re-asked rather than saved.

**Prefer `select_list`.** A tap cannot be malformed, so there is nothing to clean
afterwards and nothing to guess about intent. Typing produces `3`, `3.`, `tres`,
`la 3`, `3 por favor`, `#3` — every one a real answer from a cooperative person,
and every one needing a pattern written in advance or a hand-clean later. Neither
scales.

### `choices`

| column | what it does |
| --- | --- |
| `list_name` | ties a block of rows to the question that uses them |
| `value` | the **code** stored in the warehouse |
| `option_id` | what a tapped row sends back; blank generates one |
| `label:<lang>` | what the respondent sees. **24 characters** in a list, **20** on a button |
| `description:<lang>` | a second line under the label. 72 characters |
| `typed:<lang>` | extra spellings to accept, pipe-separated (consent only, for now) |

Set `value` explicitly on every row. A `Prefer not to say` left to its position
codes as a 6 on a 5-point scale and gets averaged in silently — give it `-99`, or
whatever your codebook uses.

24 characters is the limit that shapes question design rather than just
implementation. Every standard Likert label fits except the neutral midpoint:
`Neither agree nor disagree` is 26. Reword it. And note that 0–10 is eleven
points, so an NPS item does not fit a list picker at all.

**No emoji in a label.** It is compared literally against the reply, and a
variation selector makes two identical-looking strings different strings. Warmth
belongs in the question body, which nothing matches on.

## Worked example: one question, both ways

Same construct, asked twice. This is the ARM1/ARM2 contrast the demo exists to
measure.

`survey`:

```text
type            name  label:en                                    retries  widgets
begin group     ARM1  (relevance: ${arm}='1')                              1
text            P1    Considering the training week currently…     0        3
end group       ARM1                                                       0
begin group     ARM2  (relevance: ${arm}='2')                              1
select_list p1  P1    📅 Question 1 of 6⏎⏎Which day of the trai…   2        8
end group       ARM2                                                       0
```

`choices`:

```text
list_name  value  option_id  label:en             description:en
p1         1      p1_0       0 times              I did not use data in the last 4 weeks
p1         2      p1_1_2     1-2 times            Once or twice
p1         3      p1_3_5     3-5 times            A handful of times
p1         4      p1_6_10    6-10 times           Most weeks
p1         5      p1_gt10    More than 10 times   Almost daily
```

Both rows are called `P1` — allowed, because they are in different groups, and
that is what makes them the same question asked two ways. They become `ARM1_P1`
and `ARM2_P1`.

Note what `p1` demonstrates. The labels begin with numbers, and `0 times` sits at
position 1 — so a typed `1` could mean the label or the position, and they are
different options. The generated split therefore **refuses a bare digit** on this
question and re-asks, and the retry nudge switches to the variant that says "type
the option exactly as it appears" instead of "reply with the number". Both happen
automatically, from the labels. On a Likert running `1 - Very dissatisfied` …
`5 - Very satisfied` the two readings agree, so the digit stays accepted.

This is not hypothetical. In a live Spanish round, `p2_1` — whose label is
`1 proyecto` but which sits at position 2 — was stored as code **2**. Had the
digit been accepted, every respondent who typed `1` would have been recorded one
option off, and nothing would have reported a problem.

## Conditions and ranges

A condition decides who gets a question or a section. Each comparison is one of:

| Written | Means | Works on |
| --- | --- | --- |
| `${Q1}='2'` | is | anything; on a choice, one of its codes |
| `${Q1}!='2'` | is not | the same |
| `${Q2}>0` | is more than | a number question or a preload |
| `${Q2}<5` | is less than | the same |

Join comparisons with `and` (all must hold) or with `or` (any one), not both:
`${Q2}>0 and ${arm}='2'`. For a mix, put one part on a section around the
question and the other on the question. The builder writes these from pickers,
and each comparison becomes one split in the flow.

A number question can take a `minimum` and a `maximum`, both included: 0 and 7
for days a week. A reply outside them is asked again with the `error_range`
reply, "Please reply with a number {range}.", where `{range}` reads "from 0 to
7". A decimal is stored with a point whichever mark was typed, so `3,5` and
`3.5` are the same answer.

## The builder

```powershell
just survey builder                            # opens it in your browser
```

One HTML page, opened from disk: no install and no server. Before opening it,
the command reads your account's approved messages, so it needs a working `.env`
and stops with the setup error if there is none. The page gets a copy of that
list and holds no credentials. It shows three things at once:

- **Cards**, one per step, in order. Sections indent their steps; drag a card by
  its handle, or use the arrows, to move it. Delete is undoable.
- **The flow map**, the instrument's shape: sections with a condition become
  lanes side by side, closings fan out by outcome, and the steps every flow
  shares (launch, publish) are drawn dashed. Click a node to open its card.
- **A phone preview** of the selected step in each language, with its buttons
  or list as WhatsApp renders them, the codes it stores and the nudge it sends.

The opener and the never-replied closing are picked from the approved messages,
with their text and buttons shown, and a column chosen for each `{{1}}`.
**Insert a column** in any message adds `${name}`, which each person reads as
their own value from the sample file; **Emoji** inserts one. The texts every
question shares, such as the reminder and the stop words, are under **Standard
replies** in the survey settings.

Limits show while you type: 20 characters for a button, 24 for a list option, 72
for a description, 3 buttons, 10 list rows, 1024 for a message with options. Those numbers come
from the same code `just survey check` uses, and a test fails if the page's copy
drifts.

**Open** reads a spec's JSON; **New** starts from the documented example.
**Save** writes back to the file you opened in Chrome or Edge, and downloads a
copy in other browsers. An open-and-save with no edits leaves the file
byte-identical, so the diff a reviewer reads is only what you changed.

The builder shows limits, not everything: `just survey check` stays the check
that counts, because it runs every option pattern and the consent rules.

## The cycle

With the builder:

```powershell
just survey builder                            # Open... my_survey.json, edit, Save
just survey check my_survey.json               # must pass
git add my_survey.json
```

Then, to put it on Twilio:

```powershell
just survey content my_survey.json             # the buttons and lists it sends
just survey build my_survey.json --lang en     # -> flows/<flow_name>.json, checked
just flow header-sheets flows/my_survey.json          # writes the sheet's first row
just flow header-motherduck flows/my_survey.json           # or, for MotherDuck, its table
just flow deploy flows/my_survey.json          # a draft; nothing is sent yet
```

Two kinds of message reach the phone, and only one needs Meta:

- **Approved messages**: the opener, and the closing for people who never
  replied. They reach someone who has not written to you, so Meta reviews them.
  Create and submit them yourself (see `docs/writing-templates.md`); the builder
  then lists the approved ones to pick from, with their text, and asks which
  sample column fills each `{{1}}`. `just survey build` refuses to compile until each
  one exists and every placeholder is filled.
- **Twilio content**: the buttons and lists of your questions. They are sent
  after the person has replied, so Meta never sees them. `just survey content`
  creates them; nothing to approve.

With Excel:

```powershell
just survey new --output my_survey.xlsx   # once
# ...edit in Excel...
just survey json my_survey.xlsx                # → my_survey.json
just survey check my_survey.json               # must pass
just survey rows my_survey.json                # read the whole thing at a glance
git add my_survey.json                         # commit the JSON, not the workbook
```

**The JSON is what git carries.** The workbook is a build product: regenerate it
with `just survey excel my_survey.json` whenever you want to read or edit the
instrument. It stays gitignored on purpose — a workbook in a pull request is a
binary blob nobody can review, whereas a JSON diff is the change to the
instrument in words.

That makes the workbook → JSON direction the one that matters. An edit that does
not survive the trip back is silent data loss on your instrument, which is why it
is tested as a round trip.

## What `check` actually does

It does not read the conditions it will generate. It **runs** them, through the
same evaluator Studio uses, and checks where every possible reply lands:

- every option label, its id, and its typed position must route to the store step
- an ambiguous digit must be **refused**, not guessed at
- junk (`banana`, `""`, an out-of-range number) must not match
- whatever the split accepts, the code mapping must code as that option and not
  as `other`
- a condition compares only what it can: `>` and `<` need a number question or
  a preload and a number, `=` on a choice needs one of its codes
- a `constraint` you wrote is run against replies people actually send — `0`,
  `12`, ` 7 ` must pass; `about 5`, `tres`, `3.5` must not

A condition that looks right and matches nothing is the same class of defect as a
break-off that publishes no row: invisible in the editor, obvious only in the
data, months later.

It also prints one thing that is **not** a failure and does not affect the exit
code — a standing note to read the consent wording by hand. No check can tell
whether consent text is accurate, voluntary and comprehensible, every check above
can pass on wording that is misleading, and by the time it is wrong somebody has
already agreed to something.

## Related

- [docs/flow-design.md](flow-design.md) — the conventions behind the shape, and
  what to know before designing questions
- [docs/writing-templates.md](writing-templates.md) — the one message Meta
  reviews, and how to get it approved as UTILITY
- [docs/running-a-round.md](running-a-round.md) — every command, and what each
  check blocks on
- [docs/encryption.md](encryption.md) — what `encrypt: yes` actually does
