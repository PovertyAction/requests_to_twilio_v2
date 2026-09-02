"""Tests for choosing where a submission is written.

Two destinations occupy the same position in the flow graph: a MotherDuck
INSERT and a Google Sheets append. Which one a flow calls is a build-time
choice, and the wiring between the choice and the deployed Function is the part
that fails quietly - a widget pointing at the wrong URL still validates, still
deploys, and still returns 200 to Studio.

The cross-language half is here for the same reason `test_interop.py` exists:
Python splits the Google private key across numbered environment variables and
JavaScript joins them back, and nothing else in the suite would notice if the
two stopped agreeing. The failure would surface as an authentication error in a
Twilio Function log, during a live round.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from requests_to_twilio.flows import FlowError, sheet_header_row

REPO_ROOT = Path(__file__).resolve().parents[1]
JS_MODULE = REPO_ROOT / "twilio_functions" / "publish_gsheets.js"

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="Node.js is not installed")


def _load(name: str, path: Path):
    """Import a script that lives outside the package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo():
    return _load("demo_builder", REPO_ROOT / "scripts" / "build_data_use_demo.py")


@pytest.fixture(scope="module")
def deployer():
    return _load("deployer", REPO_ROOT / "scripts" / "deploy_twilio_functions.py")


def run_node(script: str, env: dict[str, str]) -> str:
    """Run a snippet of JS against the real Twilio Function module."""
    child_env = {**os.environ, **env}
    result = subprocess.run(  # noqa: S603
        [node, "-e", script],
        capture_output=True,
        text=True,
        env=child_env,
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}")
    return result.stdout


class TestTheHeaderRowMatchesWhatTheFlowPublishes:
    """`publish_gsheets` maps a parameter to a column by name, via row 1."""

    def test_every_published_parameter_becomes_a_column(self):
        definition = {
            "states": [
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "properties": {
                        "parameters": [
                            {"key": "caseid", "value": "{{flow.data.caseid}}"},
                            {
                                "key": "ARM1_P1",
                                "value": "{{widgets.ARM1_P1.inbound.Body}}",
                            },
                        ]
                    },
                    "transitions": [],
                }
            ]
        }
        assert sheet_header_row(definition) == "caseid,ARM1_P1,submitted_at"

    def test_submitted_at_is_appended_because_the_function_stamps_it(self):
        definition = {
            "states": [
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "properties": {"parameters": [{"key": "caseid", "value": "x"}]},
                    "transitions": [],
                }
            ]
        }
        assert sheet_header_row(definition).endswith(",submitted_at")

    def test_submitted_at_is_not_duplicated_when_the_flow_sends_it(self):
        definition = {
            "states": [
                {
                    "name": "publish_gsheets",
                    "type": "run-function",
                    "properties": {
                        "parameters": [
                            {"key": "caseid", "value": "x"},
                            {"key": "submitted_at", "value": "y"},
                        ]
                    },
                    "transitions": [],
                }
            ]
        }
        assert sheet_header_row(definition).count("submitted_at") == 1

    def test_a_flow_that_publishes_nothing_is_an_error_not_an_empty_row(self):
        # An empty header row would be pasted into a sheet and would then drop
        # every answer, silently. Refusing is the only safe answer.
        with pytest.raises(FlowError, match="publishes nothing"):
            sheet_header_row({"states": []})

    def test_the_real_english_flow_produces_a_usable_header(self):
        definition = json.loads(
            (REPO_ROOT / "flows" / "data_use_demo_en.json").read_text(encoding="utf-8")
        )
        header = sheet_header_row(definition).split(",")

        assert header[0] == "caseid"
        assert header[-1] == "submitted_at"
        # The join key back to the execution log, and the rollup an analysis
        # groups by. Both have been missing from a published payload before.
        assert "execution_sid" in header
        assert "final_status" in header
        assert len(header) == len(set(header)), (
            "a duplicated column silently overwrites"
        )


class TestThePublishTargetPicksTheRightFunction:
    """The build-time choice has to reach every place the widget is named."""

    def test_both_targets_are_offered_and_gsheets_is_the_default(self, demo):
        assert set(demo.PUBLISH_TARGETS) == {"motherduck", "gsheets"}
        assert demo.DEFAULT_PUBLISH_TARGET == "gsheets"

    def test_every_target_widget_name_is_one_flow_check_recognises(self, demo):
        # `rtt flow check` finds the publish step by name - `publish_*` or
        # anything containing `gsheet`. A target whose widget name did not match
        # would make every break-off path read as unpublished, on a flow that is
        # perfectly correct.
        from requests_to_twilio.flows import _is_publish_widget

        for target in demo.PUBLISH_TARGETS.values():
            state = {"name": target["widget"], "type": "run-function"}
            assert _is_publish_widget(state), target["widget"]

    @pytest.mark.parametrize(
        ("target", "expected_widget", "expected_path"),
        [
            ("motherduck", "publish_motherduck", "/publish-motherduck"),
            ("gsheets", "publish_gsheets", "/publish-gsheets"),
        ],
    )
    def test_resolve_functions_returns_the_chosen_targets_coordinates(
        self, demo, target, expected_widget, expected_path
    ):
        client = _FakeTwilioClient()
        resolved = demo.resolve_functions(client, target)

        assert resolved["publish_widget"] == expected_widget
        assert resolved["publish_url"].endswith(expected_path)
        assert resolved["publish_sid"] == f"ZH_{expected_widget}"
        # The encryption function is shared and must not move with the target.
        assert resolved["encrypt_sid"] == "ZH_encrypt_fields"

    def test_an_unknown_target_is_refused_before_any_api_call(self, demo):
        with pytest.raises(demo.BuildError, match="Unknown publish target"):
            demo.resolve_functions(_FakeTwilioClient(), "postgres")

    def test_a_missing_publish_function_names_the_target_that_needed_it(self, demo):
        # The realistic failure: a service deployed before this repo grew a
        # second destination. "Re-run deploy-functions" alone would not say why.
        client = _FakeTwilioClient(functions=["encrypt_fields", "publish_motherduck"])
        with pytest.raises(demo.BuildError, match="publish_gsheets.*'gsheets'"):
            demo.resolve_functions(client, "gsheets")


class TestTheBuiltFlowRoutesEveryPathToTheChosenWidget:
    """A publish widget nothing transitions to writes no rows at all."""

    @pytest.mark.parametrize("target", ["motherduck", "gsheets"])
    def test_the_publish_widget_is_named_for_the_target(self, demo, target):
        definition = _build_with_target(demo, target)
        widget = demo.PUBLISH_TARGETS[target]["widget"]

        names = [s["name"] for s in definition["states"]]
        assert widget in names
        # Exactly one. A leftover from the other target would be an orphan that
        # `flow check` reports as an unreachable publisher.
        assert sum(1 for n in names if n.startswith("publish_")) == 1

    @pytest.mark.parametrize("target", ["motherduck", "gsheets"])
    def test_no_transition_still_points_at_the_other_target(self, demo, target):
        definition = _build_with_target(demo, target)
        widget = demo.PUBLISH_TARGETS[target]["widget"]
        other = {t["widget"] for k, t in demo.PUBLISH_TARGETS.items() if k != target}

        destinations = {
            transition.get("next")
            for state in definition["states"]
            for transition in state.get("transitions", [])
        }
        assert widget in destinations
        assert not (destinations & other), (
            "a transition still names the other target, so those executions "
            "would dead-end at a widget that is not on the canvas"
        )

    @pytest.mark.parametrize("target", ["motherduck", "gsheets"])
    def test_the_encryption_failure_path_still_publishes(self, demo, target):
        # A failed encryption must still write a row - losing the answers
        # because the PII could not be sealed is the worse trade - so this
        # transition is the one most easily left pointing at the old name.
        definition = _build_with_target(demo, target)
        widget = demo.PUBLISH_TARGETS[target]["widget"]

        marker = next(
            s for s in definition["states"] if s["name"] == "mark_encrypt_failed"
        )
        assert [t["next"] for t in marker["transitions"]] == [widget]

    def test_the_published_payload_does_not_depend_on_the_target(self, demo):
        # Same columns either way. If the two payloads diverged, a round would
        # not be comparable to the round before it that used the other target.
        duck = _build_with_target(demo, "motherduck")
        sheet = _build_with_target(demo, "gsheets")

        def payload(definition):
            state = next(
                s for s in definition["states"] if s["name"].startswith("publish_")
            )
            return [p["key"] for p in state["properties"]["parameters"]]

        assert payload(duck) == payload(sheet)


class TestTheGooglePrivateKeySurvivesTheTripToTwilio:
    """Python splits it, JavaScript joins it, and neither can see the other."""

    def test_a_short_value_is_not_split(self, deployer):
        assert deployer.split_across_variables("K", "short") == {"K": "short"}

    def test_a_pem_key_is_split_because_it_cannot_fit_in_one_variable(self, deployer):
        # Twilio rejects a value over 450 bytes and an RSA 2048 PEM is ~1,700,
        # so this is not an optimisation - a single variable fails outright.
        key = _fake_pem()
        parts = deployer.split_across_variables("GOOGLE_PRIVATE_KEY", key)

        assert "GOOGLE_PRIVATE_KEY" not in parts
        assert list(parts) == [
            f"GOOGLE_PRIVATE_KEY_{i}" for i in range(1, len(parts) + 1)
        ]
        assert all(
            len(v.encode()) <= deployer.MAX_VARIABLE_BYTES for v in parts.values()
        )
        assert "".join(parts.values()) == key

    @requires_node
    def test_javascript_reassembles_exactly_what_python_split(self, deployer):
        key = _fake_pem()
        escaped = key.replace("\n", "\\n")
        parts = deployer.split_across_variables("GOOGLE_PRIVATE_KEY", escaped)
        assert len(parts) > 1, "the fixture must be long enough to actually split"

        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "process.stdout.write(m.readPrivateKey(process.env));"
        )
        assert run_node(script, parts) == key

    @requires_node
    def test_a_single_unsplit_variable_still_works(self):
        # Somebody who pasted a short key into the Console by hand.
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "process.stdout.write(m.readPrivateKey({GOOGLE_PRIVATE_KEY: 'a\\\\nb'}));"
        )
        assert run_node(script, {}) == "a\nb"

    @requires_node
    def test_a_missing_key_is_reported_not_silently_empty(self):
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "try { m.readConfig({GOOGLE_CLIENT_EMAIL: 'a@b.com', GOOGLE_SHEET_ID: 'x'});"
            "  process.stdout.write('NO ERROR'); }"
            "catch (e) { process.stdout.write(e.message); }"
        )
        assert "GOOGLE_PRIVATE_KEY" in run_node(script, {})


class TestTheSheetRowLinesUpWithTheHeader:
    """Answers land by header name, so a shifted row is silent corruption."""

    @requires_node
    def test_values_follow_the_header_order_not_the_payload_order(self):
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "const r = m.buildRow(['b', 'a'], {a: '1', b: '2'});"
            "process.stdout.write(JSON.stringify(r.values[0]));"
        )
        assert json.loads(run_node(script, {})) == ["2", "1"]

    @requires_node
    def test_an_unanswered_question_is_blank_rather_than_a_placeholder(self):
        # The previous version wrote "No Data", which every analysis then had to
        # know about - and which a respondent could type.
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "const r = m.buildRow(['a', 'b'], {a: 'x'});"
            "process.stdout.write(JSON.stringify(r.values[0]));"
        )
        assert json.loads(run_node(script, {})) == ["x", ""]

    @requires_node
    def test_an_unqualified_range_means_the_first_tab(self):
        # The historical behaviour, and correct for a one-tab workbook.
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "process.stdout.write(m.qualify(undefined, 'A1:FP1'));"
        )
        assert run_node(script, {}) == "A1:FP1"

    @requires_node
    def test_a_configured_tab_qualifies_the_range(self):
        # Without this, adding a delivery-tracking tab beside the responses can
        # silently redirect every submission into it - rows keep arriving, the
        # Function keeps returning 200, and nothing reports the change.
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "process.stdout.write(m.qualify('Responses', 'A1'));"
        )
        assert run_node(script, {}) == "'Responses'!A1"

    @requires_node
    def test_a_tab_name_with_a_quote_is_escaped(self):
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "process.stdout.write(m.qualify(\"Dan's round\", 'A1'));"
        )
        assert run_node(script, {}) == "'Dan''s round'!A1"

    @requires_node
    def test_a_parameter_with_no_column_is_reported(self):
        # The silent failure this mirrors from publish_motherduck: a question
        # added to the flow with no matching header vanishes behind a 200.
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "process.stdout.write(JSON.stringify("
            "  m.droppedParameters(['a'], {a: '1', ARM2_P5_code: '3'})));"
        )
        assert json.loads(run_node(script, {})) == ["ARM2_P5_code"]

    @requires_node
    def test_studios_own_bookkeeping_is_not_reported_as_a_dropped_answer(self):
        # `request` and `UserIdentity` arrive on every call and are not answers.
        # Reporting them would make the warning fire on every single row.
        script = (
            "const m = require('./twilio_functions/publish_gsheets.js');"
            "process.stdout.write(JSON.stringify("
            "  m.droppedParameters(['a'], {a: '1', request: {}, UserIdentity: 'x'})));"
        )
        assert json.loads(run_node(script, {})) == []


def _fake_pem() -> str:
    """Build a PEM-shaped string long enough to need splitting. Not a real key.

    The banner is assembled rather than written out. A file containing the
    literal marker trips both `gitleaks` and pre-commit's `detect-private-key`,
    and the fix for that is not to allowlist it: `.gitleaks.toml` says so in as
    many words, and muting the private-key rule in a repository that exists
    partly because a real key was published is the wrong trade for a test
    fixture's readability.

    Nothing here needs the marker anyway - the splitting and the round trip
    operate on length and newlines, not on shape.
    """
    marker = "-----BEGIN " + "PRIVATE KEY-----"
    closing = "-----END " + "PRIVATE KEY-----"
    body = "\n".join(
        ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC" + "x" * 13] * 25
    )
    return f"{marker}\n{body}\n{closing}\n"


def _build_with_target(demo, target: str) -> dict:
    """Build the English flow against a target, with fake Twilio coordinates."""
    functions = {
        "service_sid": "ZS" + "0" * 32,
        "environment_sid": "ZE" + "0" * 32,
        "encrypt_sid": "ZH_encrypt_fields",
        "publish_sid": "ZH_publish",
        "encrypt_url": "https://example-1234.twil.io/encrypt-fields",
        "publish_url": f"https://example-1234.twil.io{demo.PUBLISH_TARGETS[target]['path']}",
        "publish_widget": demo.PUBLISH_TARGETS[target]["widget"],
    }
    names = {
        demo.EN["intro_template"]: "HX" + "1" * 32,
        demo.EN["close_template"]: "HX" + "2" * 32,
        demo.consent_template_name("en"): "HX" + "3" * 32,
    }
    for index, key in enumerate(demo.templated_keys("en")):
        names[demo.question_template_name("en", key)] = f"HX{index}" + "4" * 31
    return demo.build("en", names, functions)


class _FakeFunction:
    def __init__(self, friendly_name: str):
        self.friendly_name = friendly_name
        self.sid = f"ZH_{friendly_name}"


class _FakeTwilioClient:
    """Just enough of the Serverless API for resolve_functions."""

    def __init__(self, functions: list[str] | None = None):
        self._functions = functions or [
            "encrypt_fields",
            "publish_motherduck",
            "publish_gsheets",
        ]
        self.serverless = self

    @property
    def v1(self):
        return self

    @property
    def services(self):
        return self

    def list(self):
        return [_FakeService()]

    def __call__(self, sid):
        return _FakeServiceContext(self._functions)


class _FakeService:
    unique_name = "rtt-survey"
    sid = "ZS" + "0" * 32


class _FakeEnvironment:
    sid = "ZE" + "0" * 32
    unique_name = "production"
    domain_name = "example-1234.twil.io"


class _FakeServiceContext:
    def __init__(self, functions: list[str]):
        self._functions = functions

    @property
    def environments(self):
        return _FakeList([_FakeEnvironment()])

    @property
    def functions(self):
        return _FakeList([_FakeFunction(name) for name in self._functions])


class _FakeList:
    def __init__(self, items):
        self._items = items

    def list(self):
        return self._items
