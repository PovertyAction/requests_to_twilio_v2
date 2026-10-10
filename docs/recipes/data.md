# just data

The answers that came back.

```powershell
just data decrypt responses.csv
just data check responses_decrypted.csv
just data fetch --since 2026-08-01
```

| Recipe | What it does |
|---|---|
| `check FILE` | Checks the answers: one row per person, every row matching someone in the sample, every outcome recorded. It warns rather than stops, because by now the data already exists. Run it during the round, not only after. |
| `decrypt` | Decrypts the phone numbers and private answers with your private key. The decrypted file stays out of git; keep it off shared drives too. |
| `fetch` | Downloads what Twilio recorded for each person. Use it when an answer row seems to be missing. |
| `push` | Loads a dataset into MotherDuck, adding to the table. `--mode replace` deletes the table first. |

More on this: [encryption](../encryption.md) and [publishing](../publishing.md).
