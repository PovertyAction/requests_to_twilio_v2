"""Tests for the survey builder page: it must agree with the Python side.

The builder is JavaScript in one HTML file, and the spec it edits is owned by
Python. Two things could quietly go wrong between them. The page's copy of the
limits and vocabularies could drift from spec.py, so the builder passes what the
check refuses. Worse, its save could write the JSON differently from
``spec_to_dict``: drop a field nobody taught it, or reorder keys. Then opening
and saving a survey without touching it would change the file, and the diff a
reviewer reads would be noise. The Node tests run the page's own model code
against the Python serialiser, byte for byte.
"""

import json
import os
import re
import shutil
import subprocess
from dataclasses import fields
from pathlib import Path

import pytest
from typer.testing import CliRunner

from requests_to_twilio import cli
from requests_to_twilio.builder import (
    BUILDER_HTML,
    embedded_approved,
    embedded_contract,
    render_contract,
    session_page,
)
from requests_to_twilio.spec import (
    ChoiceRow,
    MessageRow,
    Settings,
    Spec,
    SurveyRow,
    load_spec,
    spec_from_dict,
    spec_to_dict,
    starter_spec,
)

FIXTURES = Path(__file__).parent / "fixtures"
HTML = BUILDER_HTML.read_text(encoding="utf-8")

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="Node.js is not installed")


def python_json(payload: dict) -> str:
    """Return what ``save_spec`` writes for a payload read back through the spec."""
    spec = spec_from_dict(payload)
    return json.dumps(spec_to_dict(spec), indent=2, ensure_ascii=False) + "\n"


def model_source() -> str:
    """Return the page's pure model code, between its two markers."""
    match = re.search(r"// model:begin[^\n]*\n(.*?)// model:end", HTML, re.DOTALL)
    assert match, "survey_builder.html lost its model:begin/model:end markers"
    return match.group(1)


def builder_json(payload: dict) -> str:
    """Return what the page's Save writes for a payload it opened, run in Node."""
    script = (
        f"const C = {render_contract()};\n"
        f"{model_source()}\n"
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        "process.stdout.write(JSON.stringify(canonical(normalise(input)), null, 2)"
        " + '\\n');\n"
    )
    result = subprocess.run(  # noqa: S603
        [node, "-e", script],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ},
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}")
    return result.stdout


def every_field_spec() -> Spec:
    """Build a spec with every field set off its default, so none can be dropped."""
    return Spec(
        settings=Settings(
            form_id="full",
            form_title="Every field",
            languages=["en", "es"],
            default_language="es",
            functions_service="other-service",
            default_timeout=600,
            default_retries=1,
            stop_words="opening",
            reminder_after=600,
            preloads=["caseid", "arm"],
            flow_name={"en": "full_en", "es": "full_es"},
            description={"en": "d en", "es": "d es"},
        ),
        survey=[
            SurveyRow(
                type="integer",
                name="Q1",
                label={"en": "How many? 👋", "es": "¿Cuántos?"},
                role="",
                relevance="${arm}='2' and ${arm}!='3'",
                retries=0,
                timeout=120,
                constraint=r"(?:\s*-?\d+\s*)",
                constraint_message="error_numeric",
                minimum=0,
                maximum=7.5,
                timeout_message="late_q1",
                list_button={"en": "Choose"},
                template={"es": "tpl_es"},
                variables={"2": "arm", "1": "name"},
                timeout_reminder=False,
                publish=False,
                encrypt=True,
            ),
        ],
        choices=[
            ChoiceRow(
                list_name="yn",
                value="-99",
                option_id="yn_na",
                label={"en": "Prefer not to say"},
                description={"es": "No se cuenta"},
                typed={"en": "na|n/a"},
            )
        ],
        messages=[MessageRow(key="stop_words", text={"en": "stop, quit"})],
    )


# -- the contract -------------------------------------------------------------


def test_embedded_contract_matches_the_python_side():
    """Limits, vocabularies and the starter come from spec.py, not a copy."""
    assert embedded_contract(HTML) == render_contract(), (
        "survey_builder.html carries stale limits or starter content. "
        "Run: just dev builder-sync"
    )


def test_contract_starter_is_the_starter_spec():
    contract = json.loads(embedded_contract(HTML))
    assert contract["starter"] == spec_to_dict(starter_spec("en"))


# -- offline by construction -------------------------------------------------


def test_page_loads_nothing_from_the_network():
    """No CDN, no fetch: it has to open on a laptop with no connection."""
    # The SVG namespace is an identifier, never requested.
    stripped = HTML.replace("http://www.w3.org/2000/svg", "")
    assert not re.search(r"https?://", stripped)
    for banned in ("<link", "@import", "fetch(", "XMLHttpRequest", "<script src"):
        assert banned not in stripped, banned


def test_page_never_mentions_twilio_credentials():
    for name in ("TWILIO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID", "AUTH_TOKEN"):
        assert name not in HTML


# -- save writes what spec_to_dict writes ------------------------------------


@pytest.mark.node
@requires_node
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(spec_to_dict(starter_spec("en")), id="starter"),
        pytest.param(spec_to_dict(starter_spec("es")), id="starter-es"),
        pytest.param(
            json.loads((FIXTURES / "realistic_spec.json").read_text("utf-8")),
            id="realistic",
        ),
        pytest.param(spec_to_dict(every_field_spec()), id="every-field"),
    ],
)
def test_untouched_open_and_save_is_byte_identical(payload):
    assert builder_json(payload) == python_json(payload)


@pytest.mark.node
@requires_node
def test_the_page_script_parses(tmp_path):
    """A syntax error blanks the whole page, and nothing else would notice."""
    scripts = re.findall(r"<script>\n(.*?)</script>", HTML, re.DOTALL)
    assert scripts, "the page has no inline script"
    path = tmp_path / "page.js"
    path.write_text(scripts[-1], encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        [node, "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.node
@requires_node
def test_save_drops_what_spec_to_dict_drops():
    """Empty strings and default booleans are not written, exactly as in Python."""
    payload = {
        "settings": {"languages": ["en"]},
        "survey": [
            {
                "type": "text",
                "name": "Q1",
                "label": {"en": "Hi", "es": ""},
                "timeout_reminder": True,
                "publish": True,
                "encrypt": False,
                "relevance": "",
            }
        ],
        "choices": [{"list_name": "l", "value": 3, "label": {}}],
        "messages": [{"key": "k", "text": {"en": ""}}],
    }
    assert builder_json(payload) == python_json(payload)


@pytest.mark.parametrize(
    ("section", "cls"),
    [("survey", SurveyRow), ("choices", ChoiceRow), ("messages", MessageRow)],
)
def test_save_writes_every_field_the_dataclass_has(section, cls):
    """A field added to spec.py must reach the page, or Save would drop it."""
    block = re.search(
        rf"{section}: spec\.{section}\.map\(\(\w\) => clean\(\{{(.*?)\}}\)\)",
        model_source(),
        re.DOTALL,
    )
    assert block, f"could not find the {section} serialiser in the page"
    written = set(re.findall(r"(\w+):", block.group(1)))
    expected = {f.name for f in fields(cls)}
    assert expected <= written, f"page drops {sorted(expected - written)}"


def test_save_writes_every_settings_field():
    block = re.search(r"settings: \{(.*?)\n    \},", model_source(), re.DOTALL)
    assert block
    written = set(re.findall(r"(\w+):", block.group(1)))
    assert {f.name for f in fields(Settings)} <= written


# -- the command ---------------------------------------------------------------


APPROVED = [
    {
        "name": "rst2026_intro",
        "language": "en",
        "body": "Hi {{1}}, welcome. </script> is only text here.",
        "buttons": ["Start"],
        "placeholders": ["1"],
        "samples": {"1": "Priya"},
    }
]


def test_survey_builder_opens_a_copy_carrying_the_approved_messages(
    monkeypatch, tmp_path
):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda uri: opened.append(uri) or True)
    monkeypatch.setattr(cli.cfg, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_client", lambda: (object(), None))
    monkeypatch.setattr(cli, "approved_messages", lambda client: APPROVED)
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path))
    result = CliRunner().invoke(cli.app, ["survey", "builder"])
    assert result.exit_code == 0, result.output
    page = tmp_path / "rtt-survey-builder" / BUILDER_HTML.name
    assert opened == [page.as_uri()]
    assert embedded_approved(page.read_text(encoding="utf-8")) == APPROVED
    assert BUILDER_HTML.read_text(encoding="utf-8") == HTML, "the package page changed"


def test_survey_builder_stops_before_opening_without_credentials(monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda uri: opened.append(uri) or True)
    monkeypatch.setattr(cli.cfg, "load_env", lambda *a, **k: None)
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    result = CliRunner().invoke(cli.app, ["survey", "builder"])
    assert result.exit_code == 1
    assert "TWILIO_" in result.output
    assert opened == []


def test_the_package_page_carries_no_account():
    """Approved messages belong to one account; only the opened copy has them."""
    assert embedded_approved(HTML) is None


def test_a_snapshot_cannot_end_its_script_block(tmp_path):
    page = session_page(APPROVED, tmp_path)
    html = page.read_text(encoding="utf-8")
    block = html.split('id="rtt-approved">', 1)[1].split("\n</script>", 1)[0]
    assert "</script" not in block
    assert embedded_approved(html)[0]["body"].endswith("</script> is only text here.")


def test_builder_ships_inside_the_package():
    """`rtt survey builder` works from an installed wheel, not only a clone."""
    assert BUILDER_HTML.parent.name == "requests_to_twilio"
    assert BUILDER_HTML.is_file()


def test_realistic_fixture_is_a_valid_spec_for_the_builder():
    assert load_spec(FIXTURES / "realistic_spec.json").survey
