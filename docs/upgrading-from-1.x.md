# Upgrading from version 1.x

Version 2.0 is a rewrite. If you have a live 1.x project, this page is for you;
if you are starting fresh, you can ignore it.

## What changed and why

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
| Google Sheets as the store of record | MotherDuck, with no service-account key to manage |
| `pip install -r requirements.txt` | `uv sync` against a lockfile |
| Nothing checked the flow | `rtt flow check`, `rtt data-check` |

The encryption change is the one that matters. The 1.x scheme had three
independent problems, any one of which was disqualifying:

- **A shared secret.** The same AES key sat in Twilio's environment variables
  *and* on the laptop, so anyone with Console access could decrypt every response
  ever collected.
- **Predictable IVs.** The vendored CryptoJS 3.1.2 backs `WordArray.random()`
  with `Math.random()`, so every initialisation vector was predictable — which
  materially weakens AES-CBC.
- **Passphrases stretched by zero-padding.** A five-character password produced a
  key that looked 128-bit and was not.

2.0 mirrors SurveyCTO instead: asymmetric, with only a public key deployed. See
[encryption.md](encryption.md).

## Reading data you already collected

Old data still decrypts. It carries no `v2:` marker, so name the columns and
supply the old secret:

```powershell
just decrypt "old_responses.csv --columns name,phone --legacy-secret your_old_key"
```

Treat anything encrypted by 1.x as weakly protected, and prefer to re-store the
decrypted result under the new scheme rather than keeping the old ciphertext as
though it were protection.

## Upgrading a live project

Existing flows keep working until you redeploy the Function. When you do:

1. `just keygen` — generate a keypair
2. `just deploy-functions` — deploys the new Function and sets
   `ENCRYPTION_PUBLIC_KEY` from your private key
3. Responses collected after that point are v2; earlier ones stay v1

A single file can contain both, and `rtt decrypt` handles the mix: values with
the `v2:` marker use the private key, unmarked values are attempted with
`--legacy-secret`, and anything that is plainly plain text is passed through
untouched rather than being overwritten with a failure marker.

## Things that will surprise you

- **`rtt push` appends by default.** In 1.x there was no push. `--mode replace`
  issues `CREATE OR REPLACE TABLE` and destroys the target.
- **Re-running `launch` without `--resume` is refused** rather than sending to
  everyone a second time.
- **`flow deploy` refuses a flow that fails the checks.** Use `--force` if you
  genuinely mean it, but read the finding first — the checks exist because of
  defects that reached production.
- **`sample_output.csv` is now `<input-stem>_output.csv`.**
