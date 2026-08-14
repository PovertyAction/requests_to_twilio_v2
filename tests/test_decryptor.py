"""Tests for dataset decryption."""

import pandas as pd
import pytest

from requests_to_twilio.crypto import (
    KeyPair,
    encrypt,
    load_private_key,
    load_public_key,
)
from requests_to_twilio.decryptor import (
    FAILURE_MARKER,
    DecryptionError,
    decrypt_dataset,
    find_encrypted_columns,
)


@pytest.fixture
def keypair():
    return KeyPair.generate()


@pytest.fixture
def sheet(tmp_path, keypair):
    """Build a downloaded sheet: some encrypted columns, some plain ones."""
    public = load_public_key(keypair.public_b64)
    path = tmp_path / "responses.csv"
    pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "name": [encrypt("Ana", public), encrypt("Beto", public)],
            "city": [encrypt("Cali", public), encrypt("Bogota", public)],
            "consent": ["1", "1"],
        }
    ).to_csv(path, index=False)
    return path


class TestFindEncryptedColumns:
    def test_detects_only_encrypted(self, sheet):
        frame = pd.read_csv(sheet, dtype=str)
        assert find_encrypted_columns(frame) == ["name", "city"]

    def test_none_when_plain(self):
        frame = pd.DataFrame({"a": ["1"], "b": ["hello"]})
        assert find_encrypted_columns(frame) == []


class TestDecryptDataset:
    def test_decrypts_auto_detected_columns(self, sheet, keypair):
        private = load_private_key(keypair.private_b64)
        out, count, failed = decrypt_dataset(input_path=sheet, private_key=private)

        assert (count, failed) == (4, 0)
        result = pd.read_csv(out, dtype=str)
        assert list(result["name"]) == ["Ana", "Beto"]
        assert list(result["city"]) == ["Cali", "Bogota"]

    def test_leaves_plain_columns_untouched(self, sheet, keypair):
        private = load_private_key(keypair.private_b64)
        out, _, _ = decrypt_dataset(input_path=sheet, private_key=private)
        result = pd.read_csv(out, dtype=str)
        assert list(result["consent"]) == ["1", "1"]
        assert list(result["date"]) == ["2026-01-01", "2026-01-02"]

    def test_default_output_path(self, sheet, keypair):
        private = load_private_key(keypair.private_b64)
        out, _, _ = decrypt_dataset(input_path=sheet, private_key=private)
        assert out.name == "responses_decrypted.csv"

    def test_explicit_output_path(self, sheet, keypair, tmp_path):
        private = load_private_key(keypair.private_b64)
        target = tmp_path / "custom.csv"
        out, _, _ = decrypt_dataset(
            input_path=sheet, private_key=private, output_path=target
        )
        assert out == target and target.exists()

    def test_wrong_key_marks_cells_and_does_not_abort(self, sheet):
        """A whole file must not be lost to a wrong key; failures stay visible."""
        other = load_private_key(KeyPair.generate().private_b64)
        out, count, failed = decrypt_dataset(input_path=sheet, private_key=other)

        assert (count, failed) == (0, 4)
        result = pd.read_csv(out, dtype=str)
        assert set(result["name"]) == {FAILURE_MARKER}

    def test_named_columns_override_detection(self, sheet, keypair):
        private = load_private_key(keypair.private_b64)
        out, count, _ = decrypt_dataset(
            input_path=sheet, private_key=private, columns=["name"]
        )
        result = pd.read_csv(out, dtype=str)
        assert count == 2
        assert list(result["name"]) == ["Ana", "Beto"]
        # 'city' was not requested, so it stays encrypted.
        assert result["city"].str.startswith("v2:").all()

    def test_unknown_column_rejected(self, sheet, keypair):
        private = load_private_key(keypair.private_b64)
        with pytest.raises(DecryptionError, match="nope"):
            decrypt_dataset(input_path=sheet, private_key=private, columns=["nope"])

    def test_missing_file(self, tmp_path, keypair):
        private = load_private_key(keypair.private_b64)
        with pytest.raises(DecryptionError, match="not found"):
            decrypt_dataset(input_path=tmp_path / "gone.csv", private_key=private)

    def test_no_encrypted_columns_explains_legacy(self, tmp_path, keypair):
        path = tmp_path / "plain.csv"
        pd.DataFrame({"a": ["1"]}).to_csv(path, index=False)
        private = load_private_key(keypair.private_b64)
        with pytest.raises(DecryptionError, match="before"):
            decrypt_dataset(input_path=path, private_key=private)

    def test_blank_cells_survive(self, tmp_path, keypair):
        """An unanswered question arrives blank and must stay blank."""
        public = load_public_key(keypair.public_b64)
        path = tmp_path / "gaps.csv"
        pd.DataFrame({"name": [encrypt("Ana", public), ""], "id": ["1", "2"]}).to_csv(
            path, index=False
        )

        private = load_private_key(keypair.private_b64)
        out, count, failed = decrypt_dataset(input_path=path, private_key=private)

        assert (count, failed) == (1, 0)
        result = pd.read_csv(out, dtype=str, keep_default_na=False)
        assert list(result["name"]) == ["Ana", ""]

    def test_excel_input(self, tmp_path, keypair):
        public = load_public_key(keypair.public_b64)
        path = tmp_path / "responses.xlsx"
        pd.DataFrame({"name": [encrypt("Ana", public)]}).to_excel(path, index=False)

        private = load_private_key(keypair.private_b64)
        out, count, _ = decrypt_dataset(input_path=path, private_key=private)
        assert count == 1
        assert list(pd.read_csv(out, dtype=str)["name"]) == ["Ana"]

    def test_plain_values_in_an_encrypted_column_survive(self, tmp_path, keypair):
        """A column can hold both ciphertext and plain text, and must survive.

        Encryption switched on mid-round, or a publish that wrote a plaintext
        fallback, leaves a mixed column. Detection is per column - one marker
        makes the whole column a target - so deciding per column would hand the
        plain answers to `decrypt`, which raises, and they would be replaced by
        the failure marker. That is silent data loss in the output file.
        """
        public = load_public_key(keypair.public_b64)
        path = tmp_path / "mixed.csv"
        pd.DataFrame(
            {"name": [encrypt("Ana", public), "Beto typed in the clear"]}
        ).to_csv(path, index=False)

        private = load_private_key(keypair.private_b64)
        out, count, failed = decrypt_dataset(input_path=path, private_key=private)

        assert (count, failed) == (1, 0)
        result = pd.read_csv(out, dtype=str)
        assert list(result["name"]) == ["Ana", "Beto typed in the clear"]

    def test_plain_values_survive_when_columns_named(self, tmp_path, keypair):
        """The same protection applies on the explicit --columns path."""
        public = load_public_key(keypair.public_b64)
        path = tmp_path / "mixed_named.csv"
        pd.DataFrame({"name": [encrypt("Ana", public), "not encrypted"]}).to_csv(
            path, index=False
        )

        private = load_private_key(keypair.private_b64)
        out, count, failed = decrypt_dataset(
            input_path=path, private_key=private, columns=["name"]
        )

        assert (count, failed) == (1, 0)
        assert list(pd.read_csv(out, dtype=str)["name"]) == ["Ana", "not encrypted"]

    def test_a_legacy_secret_does_not_destroy_plain_text(self, tmp_path, keypair):
        """The mixed-column protection must survive a legacy secret being set.

        v1 ciphertext carries no marker, so once a legacy secret is in play
        every unmarked value gets attempted - including plain text. If a failed
        attempt were recorded as a failure, every plain answer in the file would
        become a failure marker. And because LEGACY_SECRET_KEY can sit in .env,
        that would happen on runs where nobody asked for legacy handling at all.
        """
        public = load_public_key(keypair.public_b64)
        path = tmp_path / "mixed_legacy.csv"
        pd.DataFrame(
            {
                "name": [
                    encrypt("Ana", public),
                    "AbdeCVNijCKhK7PnEEypxN3PvDyA1nGiZynDDisntU4=",
                    "Carlos in the clear",
                ]
            }
        ).to_csv(path, index=False)

        private = load_private_key(keypair.private_b64)
        out, count, failed = decrypt_dataset(
            input_path=path, private_key=private, legacy_secret="shortkey"
        )

        assert failed == 0
        assert list(pd.read_csv(out, dtype=str)["name"]) == [
            "Ana",
            "Maria Gomez",
            "Carlos in the clear",
        ]
        assert count == 2

    def test_a_wrong_key_is_still_reported_on_v2_values(self, sheet):
        """Passing plain text through must not silence a genuine key mismatch.

        A `v2:` value IS ciphertext, so failing to decrypt it is real and has to
        stay loud - otherwise the protection above would turn a wrong-key run
        into a silent no-op.
        """
        other = load_private_key(KeyPair.generate().private_b64)
        out, count, failed = decrypt_dataset(
            input_path=sheet, private_key=other, legacy_secret="shortkey"
        )
        assert (count, failed) == (0, 4)
        assert set(pd.read_csv(out, dtype=str)["name"]) == {FAILURE_MARKER}

    def test_output_over_input_is_refused(self, sheet, keypair):
        """Writing over the input destroys the only copy of the ciphertext."""
        private = load_private_key(keypair.private_b64)
        with pytest.raises(DecryptionError, match="overwrite the input"):
            decrypt_dataset(input_path=sheet, private_key=private, output_path=sheet)

    def test_output_over_input_is_refused_via_indirect_path(self, sheet, keypair):
        """The guard compares resolved paths, not the strings it was handed."""
        private = load_private_key(keypair.private_b64)
        indirect = sheet.parent / "sub" / ".." / sheet.name
        indirect.parent.mkdir(exist_ok=True)
        with pytest.raises(DecryptionError, match="overwrite the input"):
            decrypt_dataset(input_path=sheet, private_key=private, output_path=indirect)

    def test_refusing_leaves_the_input_intact(self, sheet, keypair):
        """The refusal must happen before the file is opened for writing."""
        private = load_private_key(keypair.private_b64)
        before = sheet.read_bytes()
        with pytest.raises(DecryptionError):
            decrypt_dataset(input_path=sheet, private_key=private, output_path=sheet)
        assert sheet.read_bytes() == before

    def test_legacy_columns_named_explicitly(self, tmp_path):
        """Pre-2.0 data has no marker, so it must be named and given a secret."""
        path = tmp_path / "old.csv"
        pd.DataFrame({"name": ["AbdeCVNijCKhK7PnEEypxN3PvDyA1nGiZynDDisntU4="]}).to_csv(
            path, index=False
        )

        out, count, failed = decrypt_dataset(
            input_path=path,
            private_key=None,
            columns=["name"],
            legacy_secret="shortkey",
        )
        assert (count, failed) == (1, 0)
        assert list(pd.read_csv(out, dtype=str)["name"]) == ["Maria Gomez"]
