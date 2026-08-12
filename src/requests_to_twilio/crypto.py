"""Public-key encryption for survey responses containing PII.

This mirrors SurveyCTO's model: a **public** key is deployed to the thing that
collects data, and the matching **private** key never leaves the researcher's
machine. Twilio can therefore encrypt responses but is structurally incapable of
reading them, so access to the Twilio Console does not imply access to PII.

The flip side is the same as SurveyCTO's: **lose the private key and the data is
unrecoverable.** Back it up the way you back up a SurveyCTO private key.

Two wire formats exist:

``v2`` (current)
    ``v2:`` + base64(ephemeral_public_key || nonce || ciphertext || tag).
    X25519 ECDH against a fresh ephemeral keypair per message, HKDF-SHA256 to an
    AES-256-GCM key. This is the standard sealed-box construction. Produced by
    ``twilio_functions/encrypt_fields.js`` and by :func:`encrypt` here.

``v1`` (legacy, decrypt-only)
    Bare base64(iv || ciphertext), AES-128-CBC, PKCS#7 padded, with the secret
    truncated or zero-padded to 16 bytes. This is what the pre-2.0 CryptoJS
    helper produced. It is unauthenticated, and the CryptoJS build used
    ``Math.random()`` for its IVs, so treat any v1 data as weakly protected.
    Support is retained purely so that already-collected data can be read.

New data is always written as v2. :func:`decrypt` auto-detects the format.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

#: Marker distinguishing the current format from the legacy one.
V2_PREFIX = "v2:"

#: Sizes in bytes for the v2 format.
PUBLIC_KEY_SIZE = 32
NONCE_SIZE = 12
KEY_SIZE = 32
TAG_SIZE = 16

#: Bound into the HKDF derivation and used as GCM additional authenticated data,
#: so a ciphertext cannot be replayed into a different scheme version.
_INFO = b"requests-to-twilio/v2"

_LEGACY_BLOCK_SIZE = 16


class CryptoError(Exception):
    """Raised when a key is unusable or a ciphertext cannot be decrypted."""


class KeyPair:
    """An X25519 keypair, encoded for storage as urlsafe base64."""

    def __init__(self, private_key: X25519PrivateKey) -> None:
        """Wrap an existing X25519 private key."""
        self._private = private_key

    @classmethod
    def generate(cls) -> KeyPair:
        """Create a new keypair."""
        return cls(X25519PrivateKey.generate())

    @property
    def private_b64(self) -> str:
        """The private key as urlsafe base64. Keep this secret."""
        raw = self._private.private_bytes(
            encoding=Encoding.Raw,
            format=PrivateFormat.Raw,
            encryption_algorithm=NoEncryption(),
        )
        return base64.urlsafe_b64encode(raw).decode()

    @property
    def public_b64(self) -> str:
        """The public key as urlsafe base64. Safe to paste into Twilio."""
        raw = self._private.public_key().public_bytes(
            encoding=Encoding.Raw, format=PublicFormat.Raw
        )
        return base64.urlsafe_b64encode(raw).decode()


def _decode_key(encoded: str, label: str) -> bytes:
    """Decode a base64 key and check its length."""
    text = encoded.strip()
    if not text:
        raise CryptoError(f"{label} is empty. Generate a keypair with `just keygen`.")

    try:
        # validate=True so that a typo is reported as a malformed key rather
        # than silently decoding to the wrong number of bytes.
        raw = base64.b64decode(
            text + "=" * (-len(text) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise CryptoError(
            f"{label} is not valid urlsafe base64. Generate one with `just keygen`."
        ) from exc

    if len(raw) != PUBLIC_KEY_SIZE:
        raise CryptoError(
            f"{label} must decode to {PUBLIC_KEY_SIZE} bytes, got {len(raw)}. "
            "Generate a keypair with `just keygen`; passphrases are not accepted."
        )
    return raw


def load_public_key(encoded: str) -> X25519PublicKey:
    """Load a public key from urlsafe base64."""
    return X25519PublicKey.from_public_bytes(_decode_key(encoded, "Public key"))


def load_private_key(encoded: str) -> X25519PrivateKey:
    """Load a private key from urlsafe base64."""
    return X25519PrivateKey.from_private_bytes(_decode_key(encoded, "Private key"))


def _derive(shared_secret: bytes, ephemeral_public: bytes, recipient_public: bytes):
    """Derive the AES key from the ECDH shared secret.

    Both public keys go into the salt so the derived key is bound to this exact
    pair, which is what stops a ciphertext being replayed against a different
    recipient.
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=ephemeral_public + recipient_public,
        info=_INFO,
    ).derive(shared_secret)


def encrypt(plaintext: str, recipient_public: X25519PublicKey) -> str:
    """Encrypt one value to a public key.

    Args:
        plaintext: The value to protect, e.g. a respondent's name.
        recipient_public: The researcher's public key.

    Returns:
        A ``v2:``-prefixed base64 token, safe to write into a Google Sheet.

    """
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    recipient_raw = recipient_public.public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )

    shared = ephemeral.exchange(recipient_public)
    key = _derive(shared, ephemeral_public, recipient_raw)

    nonce = os.urandom(NONCE_SIZE)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _INFO)

    return V2_PREFIX + base64.b64encode(ephemeral_public + nonce + sealed).decode()


def decrypt(
    token: str,
    private_key: X25519PrivateKey | None = None,
    *,
    legacy_secret: str | None = None,
) -> str:
    """Decrypt a value, auto-detecting the wire format.

    Args:
        token: The stored ciphertext.
        private_key: The researcher's private key, used for v2 data.
        legacy_secret: The old plain-text secret, used only for v1 data. Omit it
            to make v1 values fail rather than decrypt.

    Returns:
        The recovered plain text.

    Raises:
        CryptoError: If the token is malformed, the key is wrong, or the
            ciphertext has been tampered with.

    """
    if token.startswith(V2_PREFIX):
        if private_key is None:
            raise CryptoError(
                "Value is encrypted to a public key but no private key was given. "
                "Set ENCRYPTION_PRIVATE_KEY in your .env."
            )
        return _decrypt_v2(token[len(V2_PREFIX) :], private_key)

    if legacy_secret is None:
        raise CryptoError(
            "Value is not in the v2 format and no legacy secret was supplied. "
            "Pass --legacy-secret to read data collected before version 2.0."
        )
    return decrypt_legacy(token, legacy_secret)


def _decrypt_v2(payload: str, private_key: X25519PrivateKey) -> str:
    """Decrypt a v2 payload, the base64 body with the prefix already stripped."""
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CryptoError("Ciphertext is not valid base64.") from exc

    minimum = PUBLIC_KEY_SIZE + NONCE_SIZE + TAG_SIZE
    if len(raw) < minimum:
        raise CryptoError(
            f"Ciphertext is truncated: {len(raw)} bytes, expected at least {minimum}."
        )

    ephemeral_public = raw[:PUBLIC_KEY_SIZE]
    nonce = raw[PUBLIC_KEY_SIZE : PUBLIC_KEY_SIZE + NONCE_SIZE]
    sealed = raw[PUBLIC_KEY_SIZE + NONCE_SIZE :]

    try:
        shared = private_key.exchange(
            X25519PublicKey.from_public_bytes(ephemeral_public)
        )
    except ValueError as exc:
        raise CryptoError("Ciphertext carries a malformed ephemeral key.") from exc

    recipient_raw = private_key.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    key = _derive(shared, ephemeral_public, recipient_raw)

    try:
        plaintext = AESGCM(key).decrypt(nonce, sealed, _INFO)
    except InvalidTag as exc:
        raise CryptoError(
            "Decryption failed: wrong private key, or the value was altered "
            "after encryption. GCM authentication rejected this ciphertext."
        ) from exc

    return plaintext.decode("utf-8")


def decrypt_legacy(token: str, secret: str) -> str:
    """Decrypt a pre-2.0 AES-128-CBC value.

    Reproduces the old ``assure_length_16`` behaviour exactly, including its
    zero-padding, because that is how the existing data was encrypted.

    Args:
        token: Bare base64 of iv || ciphertext.
        secret: The original plain-text secret.

    Returns:
        The recovered plain text.

    Raises:
        CryptoError: If the value cannot be decrypted or unpadded.

    """
    key = _legacy_key(secret)

    try:
        raw = base64.b64decode(token)
    except (ValueError, binascii.Error) as exc:
        raise CryptoError("Legacy ciphertext is not valid base64.") from exc

    if len(raw) <= _LEGACY_BLOCK_SIZE or (len(raw) - _LEGACY_BLOCK_SIZE) % 16:
        raise CryptoError(
            "Legacy ciphertext has an invalid length; it may not be encrypted."
        )

    iv, body = raw[:_LEGACY_BLOCK_SIZE], raw[_LEGACY_BLOCK_SIZE:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(body) + decryptor.finalize()

    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    try:
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        # CBC has no authentication tag, so a wrong key usually surfaces here as
        # nonsense padding rather than as a clean authentication failure.
        raise CryptoError(
            "Legacy decryption failed, most likely the wrong secret key."
        ) from exc

    return plaintext.decode("utf-8", errors="replace")


def _legacy_key(secret: str) -> bytes:
    """Coerce a secret to 16 bytes the way the pre-2.0 helper did."""
    if len(secret) > 16:
        secret = secret[:16]
    elif len(secret) < 16:
        secret = secret + "0" * (16 - len(secret))
    return secret.encode("utf-8")
