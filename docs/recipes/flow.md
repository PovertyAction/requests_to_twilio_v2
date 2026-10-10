# just flow

A flow is the survey as Twilio runs it. These recipes put a flow on your account,
get the place its answers go ready, and let you look at any flow already there.

```powershell
just flow deploy flows/my_survey.json                # as a draft
just flow deploy flows/my_survey.json --publish      # live
just flow table flows/my_survey.json                 # its MotherDuck table
just flow header flows/my_survey.json                # its Google Sheet header
```

| Recipe | What it does |
|---|---|
| `deploy FILE` | Puts the flow on Twilio. Without `--publish` it is only a draft, and a draft sends nothing: Twilio always runs the published version. It refuses a flow that fails its checks. |
| `check [FILE]` | Looks for mistakes in a flow, such as a question with no way out when nobody replies, or answers that are never saved. With no file, it checks every flow on the account. |
| `list` | Lists the flows on your account. |
| `pull NAME` | Copies a flow from your account into `flows/`, so you can read or check it. |
| `table FILE` | Creates the MotherDuck table the answers go to, or adds the columns it is missing. It never removes a column, and it asks before changing anything. |
| `header FILE` | Prints the first row your Google Sheet needs. Paste it into row 1. |

Answers are saved only into columns that already exist. A new question with no
column is dropped with no error, so run `table` or `header` again after adding one.
