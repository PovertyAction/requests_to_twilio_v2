# requests-to-twilio

Run SMS and WhatsApp surveys through Twilio Studio, and get the answers back
without exposing respondents' personal information.

If you have run a SurveyCTO project, you already know this shape:

| Step | SurveyCTO | Here |
| --- | --- | --- |
| Build the instrument | XLSForm | Twilio Studio flow |
| Deploy a public key | server encryption key | `ENCRYPTION_PUBLIC_KEY` on a Twilio Function |
| Launch | load a sample | `rtt launch` |
| Monitor | server monitoring | the Google Sheet tracker |
| Retrieve | download encrypted data | download the sheet as CSV |
| Decrypt | local, private key | `rtt decrypt` |
| Analyse | your tool of choice | `rtt push` to MotherDuck |

## How the pipeline fits together

```text
  sample.xlsx
      |
      | rtt launch
      v
  Twilio Studio flow  ---- SMS/WhatsApp ---->  respondent
      |                                            |
      |<------------------ replies ----------------+
      v
  encrypt_fields.js      (encrypts with your PUBLIC key)
      v
  publish_gsheets.js
      v
  Google Sheet           (delivery dashboard + database of record)
      |
      | download as CSV
      v
  rtt decrypt            (decrypts with your PRIVATE key)
      v
  rtt push  ---------->  MotherDuck
```

The encryption exists because the Google Sheet is widely shared. Values are
encrypted inside Twilio, before they are ever written to the sheet, so anyone
with access to the sheet sees ciphertext. Only the holder of the private key can
read the responses.

## Setup

You need [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/).
On Windows the `pre-install` recipe installs both:

```powershell
just pre-install   # winget: just, uv, gh, Node.js, markdownlint
just venv          # creates .venv, installs dependencies, installs pre-commit hooks
```

Or in one step:

```powershell
just get-started
```

There is no `pip install`, no `requirements.txt`, and no Visual C++ build tools
step any more. `uv sync` reads `pyproject.toml` and `uv.lock`, so everyone gets
byte-identical dependency versions.

Then create your `.env`:

```powershell
cp .env.example .env
```

Fill in the Twilio credentials. **Nothing secret is ever passed as a
command-line flag** — arguments end up in shell history and are visible to other
processes.

## Generating your keypair

```powershell
just keygen
```

This prints a **public key** and writes a **private key** to
`rtt_private_key.txt`.

- The **public key** goes into Twilio. It can only encrypt. If it leaks,
  nothing is exposed.
- The **private key** stays on your machine and is the only thing that can read
  responses back. Point `.env` at it, and back it up somewhere
  access-controlled.

> **Lose the private key and the collected data is unrecoverable.** There is no
> reset, exactly as with a SurveyCTO private key. `keygen` refuses to overwrite
> an existing key file unless you pass `--force`.

## Deploying the Twilio Functions

In the Twilio Console, go to **Functions → Services** and create a service with
two functions.

1. **`encrypt_fields`** — paste `twilio_functions/encrypt_fields.js`.
2. **`publish_gsheets`** — paste `twilio_functions/publish_gsheets.js`, and add
   `googleapis` under **Dependencies**.

Set these under **Environment Variables** — never hard-code them into the
function source:

| Variable | Value |
| --- | --- |
| `ENCRYPTION_PUBLIC_KEY` | the public key from `just keygen` |
| `GOOGLE_CLIENT_EMAIL` | the Sheets service account address |
| `GOOGLE_PRIVATE_KEY` | its PEM key, with literal `\n` instead of newlines |
| `GOOGLE_SHEET_ID` | the target spreadsheet's ID |

In your Studio flow, place a Function widget calling `encrypt_fields`
immediately before the one calling `publish_gsheets`. Pass every value that
needs protecting as a parameter:

```text
key:   name
value: {{widgets.ask_name.inbound.Body}}
```

Every parameter is encrypted and returned under the same key, so the publish
widget reads `{{widgets.encrypt.parsed.name}}`. Unlike the pre-2.0 version,
**there is nothing to edit in the JavaScript when your questions change.**

## Running a survey

Your sample file needs a `Number` column. Any other columns can be passed to the
flow as parameters.

```powershell
# See exactly what would be sent, without sending anything
just launch "sample.xlsx --columns name,city --dry-run"

# Send for real, 50 at a time with a 5s pause between batches
just launch "sample.xlsx --columns name,city --batch-size 50 --sleep 5"
```

This writes a **delivery tracker** next to your input, `sample_output.csv`, with
one row per number. It is flushed to disk after every send, so if the run is
interrupted the record of who was already contacted survives:

```powershell
just launch "sample.xlsx --columns name,city --resume"
```

`--resume` skips numbers already sent successfully and retries only the
failures.

## Decrypting responses

Download the sheet as CSV, then:

```powershell
just decrypt "responses.csv"
```

Encrypted columns are detected automatically — they carry a `v2:` marker — so
there is no list of column names to keep in sync. The result is written to
`responses_decrypted.csv`.

A value that cannot be decrypted becomes `<DECRYPTION FAILED>` rather than
aborting the file, so one bad cell never costs you the other 5,000 rows.

The output is plain-text PII. Store it per IPA policy: a Cryptomator vault or an
access-controlled Box folder. It is gitignored, but it is your responsibility
once it is on disk.

## Reconciling against Twilio

`publish_gsheets.js` can fail on a transient Sheets error, and when it does that
respondent's row silently never appears. Since the sheet is the database of
record, it is worth checking:

```powershell
# What does Twilio think happened?
just fetch "--since 2026-08-01 --output executions.csv"

# Which executions never made it into the sheet?
just fetch "--against responses.csv --output missing.csv"
```

Two caveats. Twilio only retains execution context for about 30 days, so
reconcile during collection rather than months later. And this output is
**unencrypted** — the encryption protects the copy in Google Sheets, and was
never able to protect the copy inside Twilio.

## Loading into MotherDuck

```powershell
just push "responses_decrypted.csv --table survey_round_1"

# Leave direct identifiers behind
just push "responses_decrypted.csv --table survey_round_1 --columns caseid,city,answer"
```

Or in one step from decryption:

```powershell
just decrypt "responses.csv --to-motherduck survey_round_1"
```

Decrypted survey data is **Confidential** under IPA's data classification. Push
it only to a database approved and access-controlled for that classification,
and prefer `--columns` to leave direct identifiers out.

## Upgrading from version 1.x

This release is a rewrite. What changed and why:

| Before | Now |
| --- | --- |
| `python twilio_launcher.py --account_token ...` | `rtt launch`, credentials from `.env` |
| `python csv_decryptor.py --secret_key ...` | `rtt decrypt`, key from `.env` |
| One shared secret, in Twilio and on your laptop | Public key in Twilio, private key only on your laptop |
| AES-128-CBC, unauthenticated | AES-256-GCM, authenticated |
| CryptoJS 3.1.2, `Math.random()` IVs | Node's built-in `crypto`, proper CSPRNG |
| Short keys zero-padded to 16 bytes | Real 256-bit keys, passphrases refused |
| Output written only at the end of a run | Tracker flushed after every send, `--resume` |
| Edit the JS for each encrypted variable | Every parameter encrypted automatically |
| Decryption blocked unless on an `X:` drive | No path restriction (Boxcryptor is discontinued) |
| `logs_cleaner.py`, answers guessed by Jaccard similarity | `rtt fetch`, exact answers from the Studio API |
| `pip install -r requirements.txt` | `uv sync` against a lockfile |

### Reading data you already collected

Old data still decrypts. It carries no `v2:` marker, so name the columns and
supply the old secret:

```powershell
just decrypt "old_responses.csv --columns name,phone --legacy-secret your_old_key"
```

Treat anything encrypted by version 1.x as weakly protected: the CryptoJS build
it used generated initialisation vectors with `Math.random()`, which is not
cryptographically secure.

### If you are upgrading a live project

Existing flows keep working until you redeploy the Function. When you do:
generate a keypair, set `ENCRYPTION_PUBLIC_KEY`, and replace the function body.
Responses collected after that point are v2; earlier ones stay v1 and need
`--legacy-secret`. A single file can contain both — `rtt decrypt` handles the
mix.

## Development

```powershell
just test          # pytest
just test-cov      # with a coverage report
just lint-py       # ruff check
just fmt-all       # ruff format + markdownlint
just scan-secrets  # gitleaks over the working tree
```

The test suite includes a cross-language check: it encrypts with the real
JavaScript that runs inside Twilio, and decrypts in Python. That is the test
that catches the two halves drifting apart — a mismatch there would otherwise
only surface as unreadable production data, after collection.

Pre-commit hooks run `ruff`, `codespell`, `markdownlint` and
`detect-private-key`. Broader secret scanning with `gitleaks` runs in CI rather
than locally, because IPA-managed Windows machines block the gitleaks binary
under Application Control policy. None of this is decorative: an earlier version
of this repository shipped a live Google service-account key in its source.

## Security notes

- **Credentials live in `.env`**, which is gitignored, never in flags.
- **The private key never leaves your machine.** Twilio holds only the public
  key, so Console access does not imply access to respondent data.
- **Phone numbers are masked in all logs** by a filter on the log handler, not
  by careful f-strings. There is no verbosity flag that turns it off.
- **Ciphertext is authenticated.** A modified value fails loudly instead of
  decrypting to garbage.
- **Data files are gitignored** by pattern: `*.csv`, `*.xlsx`, `*_output.*`,
  `*_decrypted.*`, and key files.

## License

See [LICENSE](LICENSE).
