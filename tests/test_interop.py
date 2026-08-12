"""Cross-language tests: what Twilio encrypts, this package must decrypt.

The encrypting half of the pipeline is JavaScript running inside a Twilio
Function; the decrypting half is Python on a researcher's laptop. Nothing else
in the test suite would notice if the two drifted apart - a changed field order,
a different HKDF salt, a renamed info string - and the failure would only show
up as unreadable production data, after collection.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from requests_to_twilio.crypto import KeyPair, decrypt, load_private_key

JS_MODULE = (
    Path(__file__).resolve().parents[1] / "twilio_functions" / "encrypt_fields.js"
)

pytestmark = pytest.mark.node

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="Node.js is not installed")


def run_node(script: str, env: dict[str, str]) -> str:
    """Run a snippet of JS against the real Twilio Function module."""
    # Inherit the real environment; Node needs PATH and, on Windows, SystemRoot.
    child_env = {**os.environ, **env}
    result = subprocess.run(  # noqa: S603
        [node, "-e", script],
        capture_output=True,
        text=True,
        env=child_env,
        cwd=JS_MODULE.parent.parent,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}")
    return result.stdout


@pytest.fixture
def keypair():
    return KeyPair.generate()


@requires_node
@pytest.mark.parametrize(
    "value",
    [
        "Ana Maria Rodriguez",
        "+57 300 1234567",
        "Jose Munoz, Bogota",
        "1",
        "a" * 500,
    ],
    ids=["name", "phone", "accented-name", "single-digit", "long-answer"],
)
def test_node_encrypts_python_decrypts(keypair, value):
    token = run_node(
        "const m = require('./twilio_functions/encrypt_fields.js');"
        "const pub = m.loadPublicKey(process.env);"
        "process.stdout.write(m.encrypt(process.env.RTT_VALUE, pub));",
        env={"ENCRYPTION_PUBLIC_KEY": keypair.public_b64, "RTT_VALUE": value},
    )

    assert token.startswith("v2:")
    private = load_private_key(keypair.private_b64)
    assert decrypt(token, private) == value


@requires_node
def test_handler_encrypts_every_parameter(keypair):
    """The whole handler, as Twilio invokes it, not just the encrypt helper."""
    output = run_node(
        "const m = require('./twilio_functions/encrypt_fields.js');"
        "m.handler(process.env, {name: 'Ana', city: 'Cali', blank: ''},"
        "  (err, res) => { if (err) throw err;"
        "    process.stdout.write(JSON.stringify(res)); });",
        env={"ENCRYPTION_PUBLIC_KEY": keypair.public_b64},
    )

    # The handler also console.logs a summary line, which shares stdout.
    result = json.loads(output.strip().splitlines()[-1])
    private = load_private_key(keypair.private_b64)

    assert decrypt(result["name"], private) == "Ana"
    assert decrypt(result["city"], private) == "Cali"
    # An unanswered question stays empty rather than becoming ciphertext of "".
    assert result["blank"] == ""


@requires_node
def test_node_rejects_a_private_key_in_the_public_slot(keypair):
    """Pasting the wrong half of the keypair into Twilio must fail loudly.

    A 32-byte private key is indistinguishable from a public key by length, so
    this only fails because the resulting ciphertext is undecryptable. The test
    exists to document that the mistake is silent at encryption time - which is
    why keygen labels the two halves so emphatically.
    """
    token = run_node(
        "const m = require('./twilio_functions/encrypt_fields.js');"
        "const pub = m.loadPublicKey(process.env);"
        "process.stdout.write(m.encrypt('secret', pub));",
        env={"ENCRYPTION_PUBLIC_KEY": keypair.private_b64},
    )

    private = load_private_key(keypair.private_b64)
    from requests_to_twilio.crypto import CryptoError

    with pytest.raises(CryptoError):
        decrypt(token, private)


@requires_node
def test_node_rejects_malformed_key():
    with pytest.raises(AssertionError, match="must decode to 32 bytes"):
        run_node(
            "const m = require('./twilio_functions/encrypt_fields.js');"
            "m.loadPublicKey(process.env);",
            env={"ENCRYPTION_PUBLIC_KEY": "dG9vc2hvcnQ="},
        )


@requires_node
def test_node_rejects_missing_key():
    with pytest.raises(AssertionError, match="ENCRYPTION_PUBLIC_KEY is not set"):
        run_node(
            "const m = require('./twilio_functions/encrypt_fields.js');"
            "m.loadPublicKey({});",
            env={},
        )
