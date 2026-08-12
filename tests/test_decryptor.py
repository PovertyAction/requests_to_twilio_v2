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
