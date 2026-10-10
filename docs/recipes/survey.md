# just survey

You write the survey as rows, one per question, in the builder or in Excel. The
saved JSON file is the survey itself: it is the file to keep in git and to share
for review. `just survey build` turns it into a flow, the version Twilio runs.

```powershell
just survey builder                       # write it
just survey check my_survey.json          # check it
just survey content my_survey.json        # create its buttons and lists
just survey build my_survey.json --lang en
```

With Excel instead of the builder:

```powershell
just survey new --output my_survey.xlsx   # a starter with an example of every kind of row
just survey json my_survey.xlsx           # after editing, back to the JSON
just survey check my_survey.json
```

| Recipe | What it does |
|---|---|
| `builder` | Opens the survey builder in your browser. It first reads the approved messages on your account, so you pick the opener and closing from a list and see their text. It needs a working `.env`, and says so if there is none. In Chrome or Edge, Save writes straight back to the file you opened. |
| `new` | Writes a starter Excel workbook: a small working survey with one example of every kind of row. It will not overwrite an existing file. |
| `excel FILE` | Makes the Excel workbook from the JSON, replacing any older workbook. |
| `json FILE` | Turns the edited workbook back into the JSON. Anything that does not come back through this step is lost, so run it after every edit. |
| `rows FILE` | Prints the whole survey in the terminal. |
| `check FILE` | Tries every possible answer against every question, condition and limit, and reports anything that would go wrong. It also shows you the consent wording to read yourself, which no check can judge. |
| `content FILE` | Creates the survey's buttons and lists on Twilio. They need no approval, because they are only sent after the person has replied. It also reports whether the approved opener and closing exist. |
| `build FILE` | Turns the survey into a flow, checks the flow, and writes it to `flows/`. Add `--lang en` for the language, `--publish-target motherduck` to save answers in MotherDuck, or `--offline` for a copy to read without connecting to Twilio. It stops if an approved message has a `{{1}}` that nothing fills. |

Every column and every check: [writing a survey](../writing-a-survey.md).
