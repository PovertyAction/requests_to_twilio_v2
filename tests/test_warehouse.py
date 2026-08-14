"""Tests for the MotherDuck loader.

These cover validation and column selection, which run without a warehouse
connection. Anything that would actually connect is out of scope for CI, since
it needs a live MotherDuck token.
"""

import inspect

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


class TestWriteModeDefault:
    """`replace` issues CREATE OR REPLACE TABLE, so it destroys a round.

    Pointing `rtt push` at the table a flow publishes to is an ordinary thing to
    do - it is the table named in `.env` - and with a `replace` default that
    silently drops every row collected so far. Destroying the database of
    record has to be asked for explicitly.
    """

    def test_push_dataframe_defaults_to_append(self):
        assert inspect.signature(push_dataframe).parameters["mode"].default == "append"

    def test_push_file_defaults_to_append(self):
        assert inspect.signature(push_file).parameters["mode"].default == "append"

    def test_cli_defaults_to_append(self):
        from requests_to_twilio.cli import push

        assert inspect.signature(push).parameters["mode"].default == "append"


class TestPushFileReadsEverythingAsText:
    """A caseid is an identifier, not a number.

    Every other reader in the package passes `dtype=str`; `push_file` was the
    one that did not, so a caseid of `007` reached the warehouse as `7` and no
    longer joined back to the sampling frame.
    """

    def _capture(self, monkeypatch):
        captured = {}

        def fake_push(*, frame, **kwargs):
            captured["frame"] = frame
            return len(frame)

        monkeypatch.setattr("requests_to_twilio.warehouse.push_dataframe", fake_push)
        return captured

    def test_csv_leading_zeros_survive(self, tmp_path, monkeypatch):
        path = tmp_path / "data.csv"
        path.write_text("caseid,answer\n007,yes\n", encoding="utf-8")
        captured = self._capture(monkeypatch)

        push_file(path=path, table="t", database="d")

        assert list(captured["frame"]["caseid"]) == ["007"]

    def test_excel_leading_zeros_survive(self, tmp_path, monkeypatch):
        path = tmp_path / "data.xlsx"
        pd.DataFrame({"caseid": ["007"], "answer": ["yes"]}).to_excel(path, index=False)
        captured = self._capture(monkeypatch)

        push_file(path=path, table="t", database="d")

        assert list(captured["frame"]["caseid"]) == ["007"]
