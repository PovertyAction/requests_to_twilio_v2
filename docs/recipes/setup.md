# just setup

Getting a laptop and a Twilio account ready. You do this once, in this order:

```powershell
just setup install      # the tools, then the project
just setup key          # the encryption keys
just setup functions    # the two Functions, on Twilio
just setup mcp          # optional
```

| Recipe | What it does |
|---|---|
| `install` | Installs uv, gh, Node and markdownlint for your system, then the project and its pre-commit checks. Node is needed because the tests run the real Twilio code. This setup is tested with `just` 1.51. |
| `key` | Makes a pair of encryption keys. The public key goes to Twilio in the next step and encrypts phone numbers and private answers there. The private key stays on your laptop, outside git, and it is the only way to read them again. Keep a safe copy. |
| `functions` | Puts the two Functions on your Twilio account: one encrypts, the other saves each person's answers to a Google Sheet or to MotherDuck. Both always go up together, because Twilio replaces the whole set on each deploy. Run it again whenever you change `.env`. |
| `mcp` | Lets Claude Code read your Twilio account, using the keys in `.env`. Restart Claude Code afterwards and type `/mcp` to check. |
| `mcp-check` | Shows whether that connection is ready. |
