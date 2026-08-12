/**
 * Encrypts PII collected by a Twilio Studio flow, before it is published to
 * Google Sheets.
 *
 * Deploy as a Twilio Function and call it from a Function widget placed
 * immediately before the widget that publishes to Sheets. Pass every value that
 * needs protecting as a function parameter, e.g.
 *
 *     key:   name
 *     value: {{widgets.ask_name.inbound.Body}}
 *
 * Every parameter is encrypted and returned under the same key, so the next
 * widget reads {{widgets.encrypt.parsed.name}}. Unlike the pre-2.0 version,
 * there is nothing in this file to edit when your questions change.
 *
 * ---------------------------------------------------------------------------
 * REQUIRED ENVIRONMENT VARIABLE (Twilio Console > Functions > Service >
 * Environment Variables):
 *
 *   ENCRYPTION_PUBLIC_KEY   The urlsafe-base64 X25519 *public* key printed by
 *                           `just keygen`.
 *
 * This is a public key: it can only encrypt. Leaking it exposes nothing, and
 * nobody with Twilio Console access can read the responses this function
 * writes. The matching private key stays on the researcher's machine, exactly
 * as with a SurveyCTO private key - and, exactly as with SurveyCTO, if that
 * private key is lost the data cannot be recovered.
 * ---------------------------------------------------------------------------
 * Cryptography: X25519 ECDH to a fresh ephemeral keypair per message,
 * HKDF-SHA256, then AES-256-GCM - the standard sealed-box construction, built
 * entirely on Node's own `crypto`. The pre-2.0 version vendored CryptoJS 3.1.2,
 * whose WordArray.random() is backed by Math.random(); its initialisation
 * vectors were predictable, which materially weakened every value it encrypted.
 * ---------------------------------------------------------------------------
 */

const crypto = require("crypto");

const V2_PREFIX = "v2:";
const PUBLIC_KEY_SIZE = 32;
const NONCE_SIZE = 12;
const KEY_SIZE = 32;

// Bound into the HKDF derivation and used as GCM additional authenticated data.
// Must match _INFO in src/requests_to_twilio/crypto.py exactly.
const INFO = Buffer.from("requests-to-twilio/v2", "utf8");

// Node cannot import a bare 32-byte X25519 key, so raw keys are wrapped in the
// fixed SPKI DER header for the X25519 OID (1.3.101.110) before import.
const X25519_SPKI_PREFIX = Buffer.from("302a300506032b656e032100", "hex");

// Studio passes some bookkeeping alongside the parameters you configure.
// Encrypting these would corrupt the flow, so they are passed through.
const RESERVED_KEYS = new Set(["request", "UserIdentity"]);

/**
 * Decodes the recipient public key from the environment.
 * @param {object} context The Twilio Function context.
 * @returns {{keyObject: crypto.KeyObject, raw: Buffer}} The imported key and its raw bytes.
 */
function loadPublicKey(context) {
  const encoded = context.ENCRYPTION_PUBLIC_KEY;
  if (!encoded) {
    throw new Error(
      "ENCRYPTION_PUBLIC_KEY is not set. Add it to the Function Service's " +
        "environment variables. Use the PUBLIC key from `just keygen` - " +
        "never put the private key here.",
    );
  }

  const raw = Buffer.from(encoded.trim(), "base64url");
  if (raw.length !== PUBLIC_KEY_SIZE) {
    throw new Error(
      `ENCRYPTION_PUBLIC_KEY must decode to ${PUBLIC_KEY_SIZE} bytes, got ${raw.length}. ` +
        "Generate a keypair with `just keygen`; passphrases are not accepted.",
    );
  }

  const keyObject = crypto.createPublicKey({
    key: Buffer.concat([X25519_SPKI_PREFIX, raw]),
    format: "der",
    type: "spki",
  });

  return { keyObject, raw };
}

/**
 * Encrypts a single value to the recipient's public key.
 * @param {string} plaintext The value to protect.
 * @param {{keyObject: crypto.KeyObject, raw: Buffer}} recipient The public key.
 * @returns {string} A "v2:"-prefixed base64 token.
 */
function encrypt(plaintext, recipient) {
  // A fresh ephemeral keypair per message: this is what makes every ciphertext
  // independent, so compromising one reveals nothing about the others.
  const ephemeral = crypto.generateKeyPairSync("x25519");
  const ephemeralPublic = ephemeral.publicKey
    .export({ format: "der", type: "spki" })
    .subarray(X25519_SPKI_PREFIX.length);

  const shared = crypto.diffieHellman({
    privateKey: ephemeral.privateKey,
    publicKey: recipient.keyObject,
  });

  // Salt binds the derived key to this exact (ephemeral, recipient) pair.
  const salt = Buffer.concat([ephemeralPublic, recipient.raw]);
  const key = Buffer.from(
    crypto.hkdfSync("sha256", shared, salt, INFO, KEY_SIZE),
  );

  const nonce = crypto.randomBytes(NONCE_SIZE);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, nonce);
  cipher.setAAD(INFO);
  const ciphertext = Buffer.concat([
    cipher.update(plaintext, "utf8"),
    cipher.final(),
  ]);
  const tag = cipher.getAuthTag();

  // Layout must match crypto.py: ephemeral_public || nonce || ciphertext || tag.
  return (
    V2_PREFIX +
    Buffer.concat([ephemeralPublic, nonce, ciphertext, tag]).toString("base64")
  );
}

exports.handler = function (context, event, callback) {
  try {
    const recipient = loadPublicKey(context);
    const encrypted = {};

    for (const [key, value] of Object.entries(event)) {
      if (RESERVED_KEYS.has(key) || key.startsWith("_")) {
        continue;
      }
      // An unanswered question arrives as undefined. Preserve that rather than
      // encrypting the string "undefined", which would be indistinguishable
      // from a real answer once encrypted.
      if (value === undefined || value === null || value === "") {
        encrypted[key] = "";
        continue;
      }
      encrypted[key] = encrypt(String(value), recipient);
    }

    // Log key names only. The values are exactly the PII this function exists
    // to protect, and Twilio Console logs are readable account-wide.
    console.log(
      `encrypt_fields: encrypted ${Object.keys(encrypted).length} field(s)`,
    );

    return callback(null, encrypted);
  } catch (error) {
    console.error(`encrypt_fields failed: ${error.message}`);
    return callback(error);
  }
};

// Exported for the cross-language interop test; unused by Twilio at runtime.
module.exports.encrypt = encrypt;
module.exports.loadPublicKey = loadPublicKey;
