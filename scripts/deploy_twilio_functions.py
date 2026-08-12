"""Deploy the Twilio Functions this project needs, without the Console.

Creates (or reuses) a Functions Service, uploads `encrypt_fields.js` and
`publish_motherduck.js`, sets their environment variables from `.env`, installs
the `pg` dependency, builds and deploys.

Uploading function code is the awkward step: it is a multipart POST to
`serverless-upload.twilio.com`, a different host from the REST API, and the
Python SDK has no `create` for function versions at all. Everything else goes
through the SDK.

Secrets are read from `.env` and passed straight to Twilio; nothing is printed.

Run with `just deploy-functions`.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from requests_to_twilio import config as cfg  # noqa: E402
from requests_to_twilio.crypto import load_private_key  # noqa: E402

SERVICE_NAME = "rtt-survey"
UPLOAD_HOST = "https://serverless-upload.twilio.com/v1"

#: node-postgres. Pure JavaScript, so it loads in a Twilio Function; the DuckDB
#: driver would not, since it needs a native binary.
DEPENDENCIES = [{"name": "pg", "version": "8.13.1"}]

FUNCTIONS = [
    {
        "path": "/encrypt-fields",
        "file": "twilio_functions/encrypt_fields.js",
        "friendly_name": "encrypt_fields",
    },
    {
        "path": "/publish-motherduck",
        "file": "twilio_functions/publish_motherduck.js",
        "friendly_name": "publish_motherduck",
    },
]


def fail(message: str) -> None:
    """Print an error and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def public_key_from_private_file() -> str:
    """Derive the public key, so the private one never leaves this machine."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    path = REPO_ROOT / (
        cfg.optional("ENCRYPTION_PRIVATE_KEY_FILE") or "rtt_private_key.txt"
    )
    if not path.is_file():
        fail(f"No private key at {path}. Run `just keygen` first.")

    private = load_private_key(path.read_text(encoding="utf-8"))
    raw = private.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    return base64.urlsafe_b64encode(raw).decode()


#: Twilio rejects an environment variable value over 450 bytes. MotherDuck
#: tokens are JWTs of roughly 457, so the token has to be split; the function
#: rejoins MOTHERDUCK_TOKEN_1, _2, ... in order.
MAX_VARIABLE_BYTES = 440


def environment_variables() -> dict[str, str]:
    """Collect what the two functions need at runtime."""
    variables = {"ENCRYPTION_PUBLIC_KEY": public_key_from_private_file()}

    for name in ("MOTHERDUCK_HOST", "MOTHERDUCK_DATABASE", "MOTHERDUCK_TABLE"):
        value = cfg.optional(name)
        if not value:
            fail(f"{name} is not set in .env; publish_motherduck needs it.")
        variables[name] = value

    token = cfg.optional("MOTHERDUCK_TOKEN")
    if not token:
        fail("MOTHERDUCK_TOKEN is not set in .env; publish_motherduck needs it.")

    if len(token.encode()) <= MAX_VARIABLE_BYTES:
        variables["MOTHERDUCK_TOKEN"] = token
    else:
        chunks = [
            token[i : i + MAX_VARIABLE_BYTES]
            for i in range(0, len(token), MAX_VARIABLE_BYTES)
        ]
        for index, chunk in enumerate(chunks, start=1):
            variables[f"MOTHERDUCK_TOKEN_{index}"] = chunk
        print(
            f"  token is {len(token)} bytes, over Twilio's {MAX_VARIABLE_BYTES + 10} "
            f"limit; split across {len(chunks)} variables"
        )

    return variables


def upload_version(auth, service_sid: str, function_sid: str, path: str, source: Path):
    """Upload function source and return the new version SID.

    Multipart to the upload host; the SDK cannot do this.
    """
    response = requests.post(
        f"{UPLOAD_HOST}/Services/{service_sid}/Functions/{function_sid}/Versions",
        auth=auth,
        data={"Path": path, "Visibility": "private"},
        files={"Content": (source.name, source.read_bytes(), "application/javascript")},
        timeout=60,
    )
    if response.status_code >= 300:
        fail(
            f"Upload of {source.name} failed: HTTP {response.status_code} {response.text[:300]}"
        )
    return response.json()["sid"]


def main() -> None:
    """Create the service, upload both functions, build and deploy."""
    cfg.load_env()
    from twilio.rest import Client

    conf = cfg.TwilioConfig.from_env()
    client = Client(conf.account_sid, conf.auth_token)
    auth = HTTPBasicAuth(conf.account_sid, conf.auth_token)

    # 1. Service
    service = next(
        (
            s
            for s in client.serverless.v1.services.list(limit=50)
            if s.unique_name == SERVICE_NAME
        ),
        None,
    )
    if service is None:
        service = client.serverless.v1.services.create(
            unique_name=SERVICE_NAME,
            friendly_name="requests-to-twilio survey functions",
            include_credentials=True,
        )
        print(f"created service   {service.sid}  {SERVICE_NAME}")
    else:
        print(f"reusing service   {service.sid}  {SERVICE_NAME}")

    # 2. Environment
    environments = client.serverless.v1.services(service.sid).environments.list(
        limit=10
    )
    environment = next((e for e in environments if e.unique_name == "production"), None)
    if environment is None:
        environment = client.serverless.v1.services(service.sid).environments.create(
            unique_name="production", domain_suffix="prod"
        )
        print(f"created env       {environment.sid}")
    else:
        print(f"reusing env       {environment.sid}")

    # 3. Environment variables
    existing = {
        v.key: v
        for v in client.serverless.v1.services(service.sid)
        .environments(environment.sid)
        .variables.list(limit=50)
    }
    for key, value in environment_variables().items():
        if key in existing:
            client.serverless.v1.services(service.sid).environments(
                environment.sid
            ).variables(existing[key].sid).update(value=value)
            print(f"  updated variable {key}")
        else:
            client.serverless.v1.services(service.sid).environments(
                environment.sid
            ).variables.create(key=key, value=value)
            print(f"  set variable     {key}")

    # 4. Functions and their source
    version_sids = []
    deployed = {}
    known = {
        f.friendly_name: f
        for f in client.serverless.v1.services(service.sid).functions.list(limit=50)
    }
    for spec in FUNCTIONS:
        function = known.get(spec["friendly_name"])
        if function is None:
            function = client.serverless.v1.services(service.sid).functions.create(
                friendly_name=spec["friendly_name"]
            )
            print(f"created function  {function.sid}  {spec['friendly_name']}")
        else:
            print(f"reusing function  {function.sid}  {spec['friendly_name']}")

        source = REPO_ROOT / spec["file"]
        version_sid = upload_version(
            auth, service.sid, function.sid, spec["path"], source
        )
        version_sids.append(version_sid)
        deployed[spec["friendly_name"]] = (function.sid, spec["path"])
        print(f"  uploaded {spec['file']} -> {version_sid}")

    # 5. Build
    build = client.serverless.v1.services(service.sid).builds.create(
        function_versions=version_sids,
        dependencies=json.dumps(DEPENDENCIES),
    )
    print(f"build             {build.sid}  status={build.status}")

    for _ in range(60):
        build = client.serverless.v1.services(service.sid).builds(build.sid).fetch()
        if build.status in ("completed", "failed"):
            break
        time.sleep(3)

    if build.status != "completed":
        fail(f"Build {build.sid} ended as {build.status}. Check the Console logs.")
    print(f"build completed   {build.sid}")

    # 6. Deploy
    deployment = (
        client.serverless.v1.services(service.sid)
        .environments(environment.sid)
        .deployments.create(build_sid=build.sid)
    )
    print(f"deployed          {deployment.sid}")

    print("\nSIDs for scripts/build_rst2026_flow.py:\n")
    print(f'ENCRYPT_SERVICE_SID = "{service.sid}"')
    print(f'ENCRYPT_ENVIRONMENT_SID = "{environment.sid}"')
    print(f'ENCRYPT_FUNCTION_SID = "{deployed["encrypt_fields"][0]}"')
    print(f'PUBLISH_SERVICE_SID = "{service.sid}"')
    print(f'PUBLISH_ENVIRONMENT_SID = "{environment.sid}"')
    print(f'PUBLISH_FUNCTION_SID = "{deployed["publish_motherduck"][0]}"')


if __name__ == "__main__":
    main()
