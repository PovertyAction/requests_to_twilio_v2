# Where a round's data goes

A live round produces two streams, and they are not the same shape. Confusing
them is why delivery status usually ends up in somebody's terminal scrollback
and nowhere else.

| | what it answers | who writes it | when |
| --- | --- | --- | --- |
| **Data** | what did they answer? | the Twilio Function, from inside the flow | once per respondent, when the flow reaches its publish widget |
| **Tracking** | did it arrive, and did they reply? | your machine, polling the Messages API | every poll, rewritten in place |

Twilio writes the data: the row is appended the moment a submission completes.
Your machine writes the tracking: a message Meta rejected never became an
execution, so nothing inside the flow can report it.

Each stream can go to a spreadsheet or to a warehouse, independently:

```text
                 ┌─────────────┐
   flow ────────►│  publish_*  │──► data      →  Google Sheets  or  MotherDuck
                 │  (Function) │                 --publish-target
                 └─────────────┘

                 ┌─────────────┐
   rtt monitor ─►│  your PC    │──► tracking  →  Google Sheets  or  MotherDuck
                 │  (polling)  │                 --sheet         or  --table
                 └─────────────┘
```

## Picking a combination

Data and tracking are chosen independently. Three of the four combinations are
sensible.

| Data | Tracking | |
| --- | --- | --- |
| MotherDuck | Sheets | Field staff and PIs watch delivery in a spreadsheet; answers go where analysis happens. Nobody needs a warehouse account to see the round working |
| Sheets | Sheets | The default. One credential, one workbook, two tabs |
| MotherDuck | MotherDuck | A long instrument, or the 172-column ceiling in the way |
| Sheets | MotherDuck | No audience: the operational view where operators cannot look, the analytic data where analysts cannot query |

```powershell
# data -> MotherDuck, tracking -> Sheets
just build-demo-flow "--lang en --publish-target motherduck"
just monitor "--tracker my_round_output.csv --sample my_round.xlsx --sheet"

# both -> MotherDuck
just build-demo-flow "--lang en --publish-target motherduck"
just monitor "--tracker my_round_output.csv --sample my_round.xlsx --table tracking"
```

`--table` is the counterpart of `--sheet`: the delivery state is replaced in that
table after every poll. It is rewritten with `CREATE OR REPLACE TABLE`, holding
the current state of every number rather than a log, so naming the table the flow
publishes to would drop the round's submissions — `--table` refuses when it
matches `MOTHERDUCK_TABLE`.

## The two destinations

| | Google Sheets | MotherDuck |
| --- | --- | --- |
| To start | a spreadsheet and a service account | an account and a token |
| Who can read it | anybody you share it with | anybody who writes SQL |
| Column ceiling | 172, from the header-row lookup | none |
| Rate limits | the Sheets API quota, and it throttles | none in practice |
| Row arrives | append to the next free row | `INSERT` over the Postgres wire protocol |

Both are set up in [setup.md](setup.md). The target is a flag; the default only
settles which one you get by not choosing.

## Both destinations have the same silent failure

Neither publisher creates columns. A parameter whose name is not already a
column — a table column, or a header cell in row 1 — is **dropped behind an HTTP
200, into a row that looks complete.** Add a question, forget the column, and
the round collects nothing for it while reporting perfect health.

So generate the destination from the instrument rather than maintaining it
alongside:

```powershell
just flow-schema flows/data_use_demo_en.json --table my_db.main.round1  # DDL
just flow-header flows/data_use_demo_en.json                            # header row
```

**Run one of these after every instrument change.** Both Functions log the names
they had to drop, so the Twilio Console will tell you it happened. Nothing in
the data will.

## Two tabs in one workbook

`publish_gsheets` writes to the tab named by `GOOGLE_SHEET_TAB`. Set it as soon
as the workbook has more than one tab:

```dotenv
GOOGLE_SHEET_TAB=data
```

Unset, the Function appends to *the first visible tab* — which is fine for a
one-tab workbook and quietly wrong the moment you add a tracking sheet beside
it. Reordering the tabs, or hiding one, then redirects every submission into
whichever is now first, against whatever header row it happens to have. Rows
keep arriving and the Function keeps returning 200.

`rtt monitor --sheet` writes to `--sheet-tab`, default `tracking`. Both tabs
must already exist; neither writer creates one. `--table` has no such
requirement — it creates or replaces the table it is given.

## No phone numbers leave the master list

Everything published is keyed on **caseid**. An unencrypted phone number exists
in exactly two places:

1. the sample file the round was launched from
2. the dataset after `rtt decrypt`

Not the delivery tracker, not the running log, not either tab, not the
warehouse. `rtt monitor --sample` reads the master list to turn the numbers the
Messages API returns into caseids, in memory, for the length of a poll.

This is enforced, not merely intended: `sheets.py` refuses to write a column
named `number`, `phone`, `to`, `from` or `contact` at all. A written row has
been disclosed to everyone the sheet is shared with, and deleting it afterwards
does not undo that.

A number the master list does not know — somebody writing in unprompted, or a
typo in the sample — is filed under `unknown-<8 hex digits>`: stable between
polls, distinct per sender, and not reversible to a phone number by anyone
reading the sheet. Those rows stay visible, because an unexpected sender is
exactly what a live round wants to notice.

## Watching a round, end to end

```powershell
just launch my_round.xlsx --columns "caseid,name,arm"
just monitor "--tracker my_round_output.csv --sample my_round.xlsx --sheet --every 1 --hours 2"
```

Each poll rewrites the tracking destination in place. The states, the flags and
what "settled" does not mean are in
[running a round](running-a-round.md#rtt-monitor--did-the-round-actually-land).

## After the round

```powershell
just fetch "--tracker my_round_output.csv"       # reconcile against executions
just data-check my_round.csv                     # high-frequency checks
just decrypt my_round.csv                        # identifiers, locally
```

Export the data tab, or query the table, and decrypt locally. The decrypted file
is the second of the two places a plain-text number is allowed to exist — keep
it in a Cryptomator vault or an access-controlled Box folder, and never commit
it.
