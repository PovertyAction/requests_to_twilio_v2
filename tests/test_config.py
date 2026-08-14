"""Tests for environment loading.

Credential loading is the step that silently produces "not set" errors when it
goes wrong, so the precedence rules are pinned down here.
"""

import pytest

from requests_to_twilio.config import (
    ConfigError,
    TwilioConfig,
    load_env,
    optional,
    require,
)

ENV_BODY = """\
TWILIO_ACCOUNT_SID=ACfromdotenv
TWILIO_AUTH_TOKEN=tokenfromdotenv
TWILIO_NUMBER=whatsapp:+15555550100
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Build a directory containing a .env and make it the working directory."""
    (tmp_path / ".env").write_text(ENV_BODY, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_loads_from_dotenv_in_working_directory(project, monkeypatch):
    """Found via the working directory, not this module's location.

    python-dotenv's default search starts from the calling module's file, which
    lives in site-packages for a non-editable install - so the project's .env
    would never be found.
    """
    load_env()
    assert require("TWILIO_ACCOUNT_SID") == "ACfromdotenv"


def test_real_exported_value_wins(project, monkeypatch):
    """A deliberate override from the shell or CI must not be clobbered."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfromshell")
    load_env()
    assert require("TWILIO_ACCOUNT_SID") == "ACfromshell"


def test_empty_variable_does_not_shadow_dotenv(project, monkeypatch):
    """An empty variable is a placeholder, not an override.

    Regression test. `.claude/settings.local.json`, CI variable blocks and
    similar tooling export every key they know about, blank ones included.
    With plain `override=False` the blank wins and every credential reads as
    unset, which surfaces as a confusing "TWILIO_ACCOUNT_SID is not set" even
    though .env is correct.
    """
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "")
    load_env()
    assert require("TWILIO_ACCOUNT_SID") == "ACfromdotenv"


def test_whitespace_only_variable_does_not_shadow_dotenv(project, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "   ")
    load_env()
    assert require("TWILIO_ACCOUNT_SID") == "ACfromdotenv"


def test_explicit_env_file(tmp_path, monkeypatch):
    path = tmp_path / "custom.env"
    path.write_text("TWILIO_ACCOUNT_SID=ACexplicit\n", encoding="utf-8")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    load_env(path)
    assert require("TWILIO_ACCOUNT_SID") == "ACexplicit"


def test_missing_explicit_env_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="Env file not found"):
        load_env(tmp_path / "nope.env")


def test_missing_dotenv_is_not_an_error(tmp_path, monkeypatch):
    """No .env is fine when the environment is populated another way."""
    monkeypatch.chdir(tmp_path)
    load_env()


class TestRequireAndOptional:
    def test_require_rejects_unset(self, monkeypatch):
        monkeypatch.delenv("SOME_MISSING_VAR", raising=False)
        with pytest.raises(ConfigError, match="is not set"):
            require("SOME_MISSING_VAR")

    def test_require_rejects_empty(self, monkeypatch):
        monkeypatch.setenv("SOME_BLANK_VAR", "   ")
        with pytest.raises(ConfigError, match="is not set"):
            require("SOME_BLANK_VAR")

    def test_require_strips(self, monkeypatch):
        monkeypatch.setenv("SOME_VAR", "  value  ")
        assert require("SOME_VAR") == "value"

    def test_optional_returns_none_when_blank(self, monkeypatch):
        monkeypatch.setenv("SOME_BLANK_VAR", "  ")
        assert optional("SOME_BLANK_VAR") is None


class TestTwilioConfig:
    def test_from_env(self, project):
        load_env()
        conf = TwilioConfig.from_env()
        assert conf.account_sid == "ACfromdotenv"
        assert conf.from_number == "whatsapp:+15555550100"
        # Not in the fixture's .env, and optional.
        assert conf.flow_id is None

    def test_flow_id_override_wins(self, project):
        load_env()
        conf = TwilioConfig.from_env()
        assert conf.resolve_flow_id("FW" + "1" * 32) == "FW" + "1" * 32

    def test_flow_id_must_look_like_a_flow(self, project):
        """Catch an execution SID or friendly name pasted in by mistake."""
        load_env()
        conf = TwilioConfig.from_env()
        with pytest.raises(ConfigError, match="should start with 'FW'"):
            conf.resolve_flow_id("edutainment_bl")

    def test_missing_flow_id_explains(self, project):
        load_env()
        conf = TwilioConfig.from_env()
        with pytest.raises(ConfigError, match="No flow given"):
            conf.resolve_flow_id(None)

    def test_from_number_falls_back_to_env(self, project):
        load_env()
        conf = TwilioConfig.from_env()
        assert conf.resolve_from_number(None) == "whatsapp:+15555550100"
