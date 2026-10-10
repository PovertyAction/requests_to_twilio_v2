# just round

Sending the survey to the people in your sample, and watching it arrive.

```powershell
just round intake scripts/build_my_sample.py            # your list -> a sample
just round send my_sample caseid,name --dry-run         # every check, nothing sent
just round send my_sample caseid,name                   # send, then watch an hour
just round send my_sample caseid --watch motherduck     # watch in MotherDuck instead
```

| Recipe | What it does |
|---|---|
| `intake BUILDER` | Runs a small script, written once for each source of numbers, that turns your list into a sample. It fixes each number's format for its country, drops anyone who did not consent, and refuses landlines. People already in the sample keep their `caseid` and `arm`. Rows it cannot fix go to a `_needs_human_review.csv` file, with the reason. |
| `send SAMPLE COLUMNS` | Sends the survey to everyone in `SAMPLE.xlsx` who has not had it yet, then watches the round for an hour. `COLUMNS` are the sample columns the survey uses; `caseid` is always one. `--dry-run` runs every check and sends nothing. `--watch motherduck` shows the progress in MotherDuck rather than a sheet. |
| `watch` | Shows where each message is: failed, sent, delivered or answered. It catches what the answers cannot: a message WhatsApp refused never produces an answer row at all. |
| `reset SAMPLE` | Clears a round. It only reports what it would do until you add `--yes`. It never deletes a sheet's header row, because that row is how answers find their columns. |
| `launch` | The underlying send command with all its options, for anything `send` does not cover. |

A few things `send` does for you:

- Running it again sends only to people added since the last time, and retries
  any that failed. People keep signing up during a session, so this is the
  normal way to use it.
- It keeps watching for the full hour. Otherwise it would stop as soon as people
  answered the first message, a minute in.
- It checks every two minutes rather than every minute, because checking too
  often makes Twilio slow you down.

Change the sender's webhook well before you send: for a few minutes after the
change, some replies can still go to the old flow.

After a rehearsal, run `just round reset my_sample.xlsx --round --yes` before the
real round. It clears the three files that carry a round's state, so the test
numbers and rows do not mix into the real one.

The full walkthrough: [running a round](../running-a-round.md).
