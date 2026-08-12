"""Tests for the MotherDuck loader.

These cover validation and column selection, which run without a warehouse
connection. Anything that would actually connect is out of scope for CI, since
it needs a live MotherDuck token.
"""

import pandas as pd
import pytest

from requests_to_twilio.warehouse import (
    WarehouseError,
    push_dataframe,
    push_file,
    resolve_database,
)


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "caseid": ["1", "2"],
            "name": ["Ana", "Beto"],
            "answer": ["yes", "no"],
        }
    )


class TestResolveDatabase:
    def test_override_wins(self, monkeypatch):
        monkeypatch.setenv("MOTHERDUCK_DATABASE", "from_env")
        assert resolve_database("explicit") == "explicit"

    def test_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("MOTHERDUCK_DATABASE", "from_env")
        assert resolve_database(None) == "from_env"

    def test_missing_is_an_error(self, monkeypatch):
        monkeypatch.delenv("MOTHERDUCK_DATABASE", raising=False)
        with pytest.raises(WarehouseError, match="No MotherDuck database"):
            resolve_database(None)


class TestValidation:
    def test_bad_mode_rejected(self, frame):
        with pytest.raises(WarehouseError, match="mode must be one of"):
            push_dataframe(frame=frame, table="t", database="d", mode="upsert")

    def test_unknown_column_rejected(self, frame):
        with pytest.raises(WarehouseError, match="ssn"):
            push_dataframe(
                frame=frame, table="t", database="d", columns=["caseid", "ssn"]
            )

    def test_empty_frame_writes_nothing(self):
        """Must not open a connection just to push zero rows."""
        assert push_dataframe(frame=pd.DataFrame(), table="t", database="d") == 0

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(WarehouseError, match="not found"):
            push_file(path=tmp_path / "gone.csv", table="t", database="d")


def test_column_subset_is_validated_before_connecting(frame):
    """Identifier-dropping must fail fast on a typo.

    If a typo silently pushed the full frame instead, direct identifiers would
    reach the warehouse - the exact outcome the option exists to prevent.
    """
    with pytest.raises(WarehouseError):
        push_dataframe(frame=frame, table="t", database="d", columns=["caseid", "naem"])
