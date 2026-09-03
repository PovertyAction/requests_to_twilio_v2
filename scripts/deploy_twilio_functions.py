"""Deploy the Twilio Functions this project needs, without the Console.

Creates (or reuses) a Functions Service, uploads `encrypt_fields.js` and *both*
publish functions, sets their environment variables from `.env`, installs their
dependencies, builds and deploys.

Both publish targets are always uploaded, whichever one a flow currently calls.
A Twilio deployment is the complete set of functions in its build, so deploying
a subset does not leave the others alone - it removes them, and every flow
pointing at one starts 404ing mid-execution. Uploading both costs a little
build time and makes `--publish-target` a rebuild of the flow rather than a
redeployment of the account.

Credentials are per target and each is optional; what is missing is reported
rather than assumed. Deploying with only one configured is normal and fine -
the other function is present but unused, and fails loudly if a flow ever calls
it.

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

#: node-postgres for MotherDuck, googleapis for Sheets. Both are pure
#: JavaScript, so both load in a Twilio Function; the DuckDB driver would not,
#: since it needs a native binary.
#:
#: A build carries one dependency set for the whole service, so both are always
#: installed - there is no way to give one function `pg` and another
#: `googleapis`.
DEPENDENCIES = [
    {"name": "pg", "version": "8.13.1"},
    {"name": "googleapis", "version": "144.0.0"},
]

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
    {
        "path": "/publish-gsheets",
        "file": "twilio_functions/publish_gsheets.js",
        "friendly_name": "publish_gsheets",
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


#: Twilio rejects an environment variable value over 450 bytes. Two credentials
#: this project carries are longer than that and neither fits in one variable:
#:
#:   MotherDuck token       a JWT of roughly 457 bytes
#:   Google private key     an RSA 2048 PEM of roughly 1,700 bytes
#:
#: Both are split across numbered variables and rejoined in order by the
#: function that needs them.
MAX_VARIABLE_BYTES = 440


def split_across_variables(name: str, value: str) -> dict[str, str]:
    """Return ``{name: value}``, or numbered parts when it is over the cap.

    Args:
        name: Base variable name, e.g. ``GOOGLE_PRIVATE_KEY``.
        value: The credential.

    Returns:
        Either the single variable or ``NAME_1``, ``NAME_2``, ... in order.

    Chunked on bytes rather than characters. A PEM key is ASCII so the two agree
    there, but a token that is not would be cut mid-character and rejoin as
    something subtly different - a corruption that only shows up as an
    authentication failure with no clue as to why.

    """
    encoded = value.encode()
    if len(encoded) <= MAX_VARIABLE_BYTES:
        return {name: value}

    chunks = [
        encoded[i : i + MAX_VARIABLE_BYTES].decode("utf-8", errors="strict")
        for i in range(0, len(encoded), MAX_VARIABLE_BYTES)
    ]
    print(
        f"  {name} is {len(encoded)} bytes, over Twilio's 450-byte limit; "
        f"split across {len(chunks)} variables"
    )
    return {f"{name}_{index}": chunk for index, chunk in enumerate(chunks, start=1)}


def motherduck_variables() -> tuple[dict[str, str], list[str]]:
    """Collect what publish_motherduck needs. Returns (variables, missing)."""
    wanted = (
        "MOTHERDUCK_HOST",
        "MOTHERDUCK_DATABASE",
        "MOTHERDUCK_TABLE",
        "MOTHERDUCK_TOKEN",
    )
    found = {name: cfg.optional(name) for name in wanted}
    missing = [name for name, value in found.items() if not value]
    if missing:
        return {}, missing

    variables = {name: found[name] for name in wanted if name != "MOTHERDUCK_TOKEN"}
    variables.update(
        split_across_variables("MOTHERDUCK_TOKEN", found["MOTHERDUCK_TOKEN"])
    )
    return variables, []


def escape_newlines(pem: str) -> str:
    r"""Return a PEM key in the literal-``\n`` form a Twilio variable can hold.

    Args:
        pem: The key, with either real newlines or literal backslash-n already.

    Returns:
        The key with every line break as a two-character ``\\n`` sequence.

    Twilio environment variables cannot contain a real newline, so the PEM
    travels escaped and ``publish_gsheets.js`` restores it. Which form it starts
    in depends on how it was stored - a `.env` value written across real lines
    arrives with real newlines, one copied out of a JSON key file arrives
    already escaped - and escaping an already-escaped key turns every ``\\n``
    into ``\\\\n``, which parses as a corrupt PEM and authenticates as nothing.

    """
    return pem if "\n" not in pem else pem.replace("\n", "\\n")


def _service_account_from_file(key_file: str) -> tuple[str, str]:
    """Read ``client_email`` and ``private_key`` out of a Google JSON key."""
    path = Path(key_file)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file():
        fail(
            f"GOOGLE_SERVICE_ACCOUNT_FILE points at {path}, which does not "
            f"exist. Download the service account's JSON key from Google Cloud "
            f"(IAM > Service Accounts > Keys) and set the path to it."
        )

    try:
        account = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")

    client_email = account.get("client_email")
    private_key = account.get("private_key")
    if not client_email or not private_key:
        fail(
            f"{path} has no 'client_email'/'private_key'. That file should be "
            f"the service account key JSON, not the OAuth client secrets - the "
            f"latter has a 'web' or 'installed' top-level key instead."
        )
    return client_email, private_key


def google_variables() -> tuple[dict[str, str], list[str]]:
    """Collect what publish_gsheets needs. Returns (variables, missing).

    The credential can be supplied two ways, and the inline pair is checked
    first because it is what the accounts running this today already use:

        GOOGLE_EMAIL       the service account address
        GOOGLE_JWT_TOKEN   its PEM private key

    ``GOOGLE_JWT_TOKEN`` is a misnomer inherited from the Twilio Console, kept
    because renaming it would break every `.env` already in circulation. The
    value is not a JWT - it is the RSA private key that *signs* the JWT
    assertion exchanged for an access token, which is why Google's own client
    class is called ``JWT``. ``GOOGLE_PRIVATE_KEY`` is accepted as an alias for
    anyone who finds the original name as confusing as it is.

    The alternative is ``GOOGLE_SERVICE_ACCOUNT_FILE``, a path to the JSON key
    Google Cloud hands you at creation. That form keeps the key out of `.env`
    entirely and is the better habit for a new setup.

    Either way the key ends up in Twilio environment variables rather than in
    the Function's source. That is the whole point of the retrofit: the 18
    legacy publish functions can each carry a copy of the key
    inline, which is how one of them reached a public GitHub repository.
    """
    sheet_id = cfg.optional("GOOGLE_SHEET_ID")
    client_email = cfg.optional("GOOGLE_EMAIL") or cfg.optional("GOOGLE_CLIENT_EMAIL")
    private_key = cfg.optional("GOOGLE_JWT_TOKEN") or cfg.optional("GOOGLE_PRIVATE_KEY")
    key_file = cfg.optional("GOOGLE_SERVICE_ACCOUNT_FILE")

    if key_file and not (client_email and private_key):
        client_email, private_key = _service_account_from_file(key_file)

    missing = []
    if not sheet_id:
        missing.append("GOOGLE_SHEET_ID")
    if not client_email:
        missing.append("GOOGLE_EMAIL")
    if not private_key:
        missing.append("GOOGLE_JWT_TOKEN")
    if missing:
        return {}, missing

    # Catch a key that is not one here, where the message can say so, rather
    # than as an opaque authentication failure inside a Twilio Function during
    # a live round.
    if "PRIVATE KEY" not in private_key:
        # The banner is assembled rather than written out: a literal one in this
        # file trips the secret scan, and the remedy for that is never to
        # allowlist it. Same reason as the fixture in tests/test_publish_target.
        banner = "-----BEGIN " + "PRIVATE KEY-----"
        fail(
            f"GOOGLE_JWT_TOKEN does not look like a PEM private key - it should "
            f"begin '{banner}'. Despite the name it is not a JWT and not an "
            f"access token: those expire within the hour, and the Function needs "
            f"to mint a fresh one on every submission."
        )

    variables = {"GOOGLE_SHEET_ID": sheet_id, "GOOGLE_CLIENT_EMAIL": client_email}

    # Optional, and worth setting the moment the workbook has a second tab. An
    # unqualified range means the *first visible tab*, so adding a delivery
    # tracking sheet beside the responses - or reordering them - silently
    # redirects every submission into it, against whatever header row it has.
    tab = cfg.optional("GOOGLE_SHEET_TAB")
    if tab:
        variables["GOOGLE_SHEET_TAB"] = tab
        print(f"  target tab        {tab}")
    else:
        print(
            "  target tab        (unset - writes to the first tab; set "
            "GOOGLE_SHEET_TAB once the workbook has more than one)"
        )

    variables.update(
        split_across_variables("GOOGLE_PRIVATE_KEY", escape_newlines(private_key))
    )
    print(f"  service account   {client_email}")
    return variables, []


def environment_variables() -> dict[str, str]:
    """Collect what the deployed functions need at runtime.

    Every publish target whose credentials are present is configured; the rest
    are reported and skipped. Only a deployment with no publish target at all is
    an error - that is a service that can encrypt a respondent's identifiers and
    then has nowhere to write the row.
    """
    variables = {"ENCRYPTION_PUBLIC_KEY": public_key_from_private_file()}

    configured = []
    for label, collect in (
        ("motherduck", motherduck_variables),
        ("gsheets", google_variables),
    ):
        found, missing = collect()
        if missing:
            print(f"  {label:16} not configured - .env has no {', '.join(missing)}")
            continue
        variables.update(found)
        configured.append(label)
        print(f"  {label:16} configured")

    if not configured:
        fail(
            "No publish target is configured, so a completed survey would have "
            "nowhere to write its row. Set either the MOTHERDUCK_* variables or "
            "GOOGLE_SHEET_ID plus GOOGLE_SERVICE_ACCOUNT_FILE in .env."
        )

    return variables


#: How the Functions are exposed. The three settings are not shades of the same
#: thing:
#:
#:   private    not reachable over HTTP at all - only from another Function in
#:              the same service. A Studio Run Function widget calling one gets
#:              403 "Unauthorized", which reads like a credentials problem and
#:              is not.
#:   protected  reachable over HTTP, but only with a valid X-Twilio-Signature.
#:              Studio signs the requests it makes, so this is the setting that
#:              works for a flow.
#:   public     reachable by anyone who knows the URL.
#:
#: `protected` rather than `public` because publish_motherduck writes rows to
#: the warehouse: public would leave an unauthenticated write endpoint open to
#: anyone who saw the URL in a flow definition.
#:
#: This was `private` for the first live test, so every execution reached the
#: end of the survey and then failed to encrypt or publish anything. The
#: respondent saw a normal closing message; the data never existed.
FUNCTION_VISIBILITY = "protected"


def upload_version(auth, service_sid: str, function_sid: str, path: str, source: Path):
    """Upload function source and return the new version SID.

    Multipart to the upload host; the SDK cannot do this.
    """
    response = requests.post(
        f"{UPLOAD_HOST}/Services/{service_sid}/Functions/{function_sid}/Versions",
        auth=auth,
        data={"Path": path, "Visibility": FUNCTION_VISIBILITY},
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

    # Nothing here has to be copied anywhere. The flow builder resolves all of
    # it from the service's unique name at build time, which is what lets the
    # same repository build a working flow on somebody else's account. This
    # block used to print six SIDs to paste into the flow builder by hand
    # - a file that no longer exists.
    print()
    print(f"service           {service.sid}  {SERVICE_NAME}")
    print(f"environment       {environment.sid}")
    print(f"domain            {environment.domain_name}")
    for spec in FUNCTIONS:
        name = spec["friendly_name"]
        print(f"  {name:16} {deployed[name][0]}  {spec['path']}")
    print()
    print("Next: just build-demo-flow   (resolves these by name; nothing to paste)")


if __name__ == "__main__":
    main()
