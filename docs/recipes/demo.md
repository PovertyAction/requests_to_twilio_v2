# just demo

A ready-made example survey, to see the whole path work on your own account
before writing your own.

```powershell
just demo content                         # its buttons and lists
just demo build --lang en                 # writes flows/data_use_demo_en.json
just flow deploy flows/data_use_demo_en.json --publish
```

| Recipe | What it does |
|---|---|
| `build` | Builds the example flow. `--lang en` or `--lang es` builds one language; `--publish-target motherduck` saves answers in MotherDuck rather than a Google Sheet. It needs your Twilio keys in `.env`. |
| `content` | Creates the example's buttons and lists, skipping any that already exist. Safe to run again. |
| `content-sync` | Recreates any of them whose wording changed in the repo. Run it after changing a question: Twilio cannot edit a button or list, and `content` leaves the old wording in place. |

About the example itself: [the demo instrument](../demo-instrument.md).
