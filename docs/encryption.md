# Encryption, and what it does not protect

Read this before writing an IRB protocol section. The short version: encryption
here protects the **published dataset**, not the **messaging platform**, and a
protocol that claims otherwise will not survive contact with someone who reads
`rtt fetch --help`.

## For the protocol

> Survey responses are collected over WhatsApp through Twilio Studio. Before any
> response is written to the study's data store, fields designated as directly
> identifying are encrypted inside Twilio's serverless environment using a public
> encryption key. The matching private key is generated on the researcher's own
> machine, is never uploaded, and is the only key capable of reading those fields.
> This is the same custody model as SurveyCTO's server-side encryption: the
> collection platform can write identifiers but cannot read them, so access to the
> Twilio account does not confer access to identifiers in the published dataset.
> Each encrypted value uses a freshly generated, single-use key, so two
> respondents who give the same answer produce different ciphertext and cannot be
> matched to each other without the private key. Each value carries a
> cryptographic authentication tag, so a value altered in storage fails to decrypt
> rather than decrypting to something wrong. Decryption occurs only on the
> researcher's machine. Loss of the private key renders the encrypted fields
> permanently unrecoverable; the key is backed up under the same procedure as a
> SurveyCTO private key.
>
> Encryption is applied at the point of publication, not at the point of receipt.
> Twilio separately retains the raw inbound message text, the respondent's
> WhatsApp number, and the preloaded sample data in its own Execution and Message
> records, in plain text, for approximately 30 days. Encryption protects the
> published dataset; it does not and cannot protect the copy held inside the
> telecom platform during that window. The controls that apply there are access
> control on the Twilio account, role scoping, and the retention period itself.

Say **pseudonymised**, not de-identified. `caseid` is published in clear beside
the encrypted number, so anyone holding the sampling frame re-identifies every
row without touching the private key. That is normal and fine — it is what makes
the data joinable — but it is not de-identification.

## The technical spec

| | |
| --- | --- |
| Scheme | X25519 ECDH → HKDF-SHA256 → AES-256-GCM (a sealed box) |
| Keys | X25519, 32 bytes each half, generated from the OS CSPRNG |
| Ephemeral key | fresh keypair **per value**, not per message or per respondent |
| KDF | HKDF-SHA256, 32-byte output, salt = `ephemeral_pub ‖ recipient_pub`, info = `requests-to-twilio/v2` |
| Cipher | AES-256-GCM, 12-byte random nonce, 16-byte tag |
| AEAD additional data | the same `requests-to-twilio/v2` string, passed as GCM AAD. Reimplement without it and this code rejects your ciphertext |
| Wire format | `v2:` + **standard** base64(`ephemeral_pub`(32) ‖ `nonce`(12) ‖ ciphertext ‖ `tag`(16)) |
| Key encoding | **urlsafe** base64 — a different alphabet from the token body above |
| Sender authentication | none — a sealed box is anonymous by construction |
| Public key lives | a Twilio Function environment variable, readable in the Console |
| Private key lives | `rtt_private_key.txt` on the researcher's machine |

Two implementations have to agree forever: `src/requests_to_twilio/crypto.py`
and `twilio_functions/encrypt_fields.js`. `tests/test_interop.py` spawns a real
`node` process, `require`s the actual Function file, encrypts with it and
decrypts in Python. CI runs it. That test is the only thing standing between a
refactor and a round of unreadable data.

## What it protects against

1. **A Twilio Console user reading identifiers out of the published dataset.**
   The Function holds only a public key. This is the main win and it is real.
2. **A leaked warehouse token, an over-shared spreadsheet, a stolen CSV export.**
   The identifier columns stay opaque.
3. **Frequency analysis.** A fresh ephemeral key per value means two identical
   names produce different ciphertext.
4. **Tampering.** GCM rejects a modified value rather than decrypting it to
   something plausible.

## What it does not protect against

**Twilio-side plaintext, for about 30 days.** This is the part to state up front.

| Where | What is there in plain text |
| --- | --- |
| Message records (inbound) | the raw body of every reply, and the respondent's number |
| Message records (outbound) | the respondent's name, interpolated into the opener |
| Studio ExecutionContext | every answer, plus all of `flow.data` — caseid, name, preloaded number |
| Studio ExecutionStep records | the values passed *into* the encryption widget |

`rtt fetch` reads exactly this. The command exists for reconciliation — finding
respondents whose row never arrived — and it is also the standing proof that the
plaintext is there. Anyone with account access can reconstruct the complete
un-encrypted dataset during the retention window using the same API call.

This is not a flaw in the design. A messaging platform has to receive plaintext
in order to deliver it. Encryption moves the trust boundary off long-term storage
and off the warehouse; it cannot move it off the carrier.

**The `flow.data` row is the one you can do something about.** Those values are
plaintext because `rtt launch` sends them that way, not because they have to be.
A column encrypted on your own machine before it enters the sample is ciphertext
in the API call, in the ExecutionContext and in the published row — Twilio never
holds the plaintext of it at all, which is strictly more than
`encrypt_fields.js` can offer for a value the respondent typed. It only works
for a value the flow passes through rather than acts on, so it does not help the
number (Twilio needs it to deliver) or a name the opener greets with. See
[Your own IDs, and what Twilio actually needs](running-a-round.md#your-own-ids-and-what-twilio-actually-needs)
in the round guide. The shortest version of this whole section: send as few
columns as the flow can work with.

**Two more limits worth stating:**

- **Free-text answers are published in clear.** Only direct identifiers are
  encrypted — name, phone, address, date of birth. Encrypting `age` or
  `education` would break monitoring without protecting much, since the
  identifiers are what re-identify someone. But respondents do put identifying
  detail into open answers. Decide who reviews them, and when.
- **Anyone with the public key can mint a valid row.** Sealed boxes have no
  sender authentication, and the public key sits in a Console environment
  variable. Confidentiality from Twilio: yes. Authenticity: no.

## Operational requirements

Things an IRB will reasonably ask for, that cryptography does not supply:

- **Named custodian for the private key**, and a named backup location. There is
  no escrow and no reset.
- **A limited, named list of people with Twilio Console access**, since that is
  the control that governs the retention window.
- **A rule for the decrypted output.** `rtt decrypt` writes plain-text PII to
  disk. That file is the most sensitive artefact in the pipeline and nothing in
  the tooling constrains where it goes.
- **A key-compromise plan.** Today the honest one is short: the wire format
  carries no key identifier, so there is no rotation — a new key means new data
  only, and old data still needs the old key. Keep both.

## Reading version 1.x data

Data collected before 2.0 used a single shared secret with AES-128-CBC,
unauthenticated, with initialisation vectors from `Math.random()`. It still
decrypts:

```powershell
just decrypt "old.csv --columns name,phone --legacy-secret your_old_key"
```

Treat it as weakly protected. The IVs were not cryptographically random, and the
same key that encrypted also decrypted and sat in the Twilio Console.
