"""Tests for the encryption layer."""

import base64

import pytest

from requests_to_twilio.crypto import (
    V2_PREFIX,
    CryptoError,
    KeyPair,
    decrypt,
    decrypt_legacy,
    encrypt,
    load_private_key,
    load_public_key,
)


@pytest.fixture
def keypair():
    return KeyPair.generate()


@pytest.fixture
def keys(keypair):
    return (
        load_public_key(keypair.public_b64),
        load_private_key(keypair.private_b64),
    )


def test_round_trip(keys):
    public, private = keys
    assert decrypt(encrypt("Ana Rodriguez", public), private) == "Ana Rodriguez"


def test_round_trip_unicode(keys):
    public, private = keys
    value = "Jose Munoz, Bogota - 100% agree"
    assert decrypt(encrypt(value, public), private) == value


def test_round_trip_empty_string(keys):
    public, private = keys
    assert decrypt(encrypt("", public), private) == ""


def test_output_is_prefixed(keys):
    public, _ = keys
    assert encrypt("x", public).startswith(V2_PREFIX)


def test_same_plaintext_gives_different_ciphertext(keys):
    """A fresh ephemeral key per message means no two tokens should match.

    If they did, an observer could tell which respondents gave the same answer
    without decrypting anything.
    """
    public, _ = keys
    assert encrypt("yes", public) != encrypt("yes", public)


def test_wrong_key_is_rejected(keys):
    public, _ = keys
    other = load_private_key(KeyPair.generate().private_b64)
    with pytest.raises(CryptoError, match="wrong private key"):
        decrypt(encrypt("secret", public), other)


def test_tampered_ciphertext_is_rejected(keys):
    """GCM authentication must catch edits. The pre-2.0 CBC scheme could not."""
    public, private = keys
    token = encrypt("original", public)

    raw = bytearray(base64.b64decode(token[len(V2_PREFIX) :]))
    raw[-1] ^= 0x01
    tampered = V2_PREFIX + base64.b64encode(bytes(raw)).decode()

    with pytest.raises(CryptoError):
        decrypt(tampered, private)


def test_truncated_ciphertext_is_rejected(keys):
    _, private = keys
    short = V2_PREFIX + base64.b64encode(b"tooshort").decode()
    with pytest.raises(CryptoError, match="truncated"):
        decrypt(short, private)


def test_malformed_base64_is_rejected(keys):
    _, private = keys
    with pytest.raises(CryptoError, match="base64"):
        decrypt(V2_PREFIX + "not base64!!", private)


def test_v2_without_private_key_is_rejected(keys):
    public, _ = keys
    with pytest.raises(CryptoError, match="no private key"):
        decrypt(encrypt("x", public), None)


def test_non_v2_without_legacy_secret_is_rejected(keys):
    _, private = keys
    with pytest.raises(CryptoError, match="not in the v2 format"):
        decrypt("cGxhaW4gb2xkIGJhc2U2NA==", private)


class TestKeyValidation:
    def test_generated_keys_are_32_bytes(self, keypair):
        assert len(base64.urlsafe_b64decode(keypair.public_b64)) == 32
        assert len(base64.urlsafe_b64decode(keypair.private_b64)) == 32

    def test_empty_key_rejected(self):
        with pytest.raises(CryptoError, match="empty"):
            load_private_key("")

    def test_non_base64_key_rejected(self):
        with pytest.raises(CryptoError, match="base64"):
            load_private_key("this is not base64 $$$")

    def test_short_key_rejected(self):
        """A passphrase must not be silently stretched into a key.

        The pre-2.0 scheme padded short secrets with '0' characters, so a
        five-character password produced a key that looked 128-bit but was not.
        """
        with pytest.raises(CryptoError, match="must decode to 32 bytes"):
            load_private_key(base64.urlsafe_b64encode(b"short").decode())

    def test_keypair_public_and_private_differ(self, keypair):
        assert keypair.public_b64 != keypair.private_b64


class TestLegacy:
    """The pre-2.0 AES-128-CBC format, kept readable for already-collected data."""

    # Produced by the original CryptoJS helper with the secret "shortkey".
    TOKEN = "AbdeCVNijCKhK7PnEEypxN3PvDyA1nGiZynDDisntU4="
    SECRET = "shortkey"
    PLAINTEXT = "Maria Gomez"

    def test_decrypts_original_ciphertext(self):
        assert decrypt_legacy(self.TOKEN, self.SECRET) == self.PLAINTEXT

    def test_auto_detected_via_decrypt(self, keys):
        _, private = keys
        result = decrypt(self.TOKEN, private, legacy_secret=self.SECRET)
        assert result == self.PLAINTEXT

    def test_key_padding_matches_old_behaviour(self):
        """'shortkey' was zero-padded to 16 bytes, so the padded form must work."""
        padded = self.SECRET + "0" * (16 - len(self.SECRET))
        assert decrypt_legacy(self.TOKEN, padded) == self.PLAINTEXT

    def test_key_truncation_matches_old_behaviour(self):
        """Secrets longer than 16 characters were truncated, not hashed."""
        assert decrypt_legacy(self.TOKEN, self.SECRET + "0" * 8 + "IGNORED") == (
            self.PLAINTEXT
        )

    def test_wrong_secret_rejected(self):
        with pytest.raises(CryptoError):
            decrypt_legacy(self.TOKEN, "wrongkey")

    def test_invalid_length_rejected(self):
        with pytest.raises(CryptoError, match="invalid length"):
            decrypt_legacy(base64.b64encode(b"tiny").decode(), self.SECRET)
