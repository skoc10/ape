"""Realistic but fake secrets for the masking corpus.

The values are built at import time from a fixed seed instead of being written
into the corpus files: the corpus stays free of anything a secret scanner would
flag, and the tests still know exactly which strings must never survive masking.
"""

import base64
import hashlib
import json
import string

_UPPER_DIGITS = string.ascii_uppercase + string.digits
_ALNUM = string.ascii_letters + string.digits


def _stream(name: str, alphabet: str, length: int) -> str:
    out: list[str] = []
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(f"ape-corpus:{name}:{counter}".encode()).digest()
        out.extend(alphabet[b % len(alphabet)] for b in block)
        counter += 1
    return "".join(out[:length])


def _mixed(name: str, length: int) -> str:
    """Alphanumeric with guaranteed upper case, lower case and digit."""
    return "aZ9" + _stream(name, _ALNUM, length - 3)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pem(label: str, name: str) -> str:
    body = _stream(name, _ALNUM + "+/", 192)
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return "\n".join([f"-----BEGIN {label}-----", *lines, f"-----END {label}-----"])


_GCP_KEY = _pem("PRIVATE KEY", "gcp")

FAKES: dict[str, str] = {
    "AWS_KEY": "AKIA" + _stream("aws-key", _UPPER_DIGITS, 16),
    "AWS_SECRET": _mixed("aws-secret", 40),
    "GITHUB_PAT": "ghp_" + _stream("github-pat", _ALNUM, 36),
    "GITHUB_FINE": "github_pat_"
    + _stream("gh-fine-a", _ALNUM, 22)
    + "_"
    + _stream("gh-fine-b", _ALNUM, 59),
    "SLACK_BOT": "xoxb-"
    + _stream("slack-a", string.digits, 12)
    + "-"
    + _stream("slack-b", _ALNUM, 24),
    "STRIPE_LIVE": "sk_live_" + _stream("stripe-live", _ALNUM, 24),
    "STRIPE_TEST": "sk_test_" + _stream("stripe-test", _ALNUM, 24),
    "JWT": ".".join(
        [
            _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()),
            _b64url(
                json.dumps({"sub": "svc-billing", "role": "admin", "iat": 1790000000}).encode()
            ),
            _stream("jwt-signature", _ALNUM + "-_", 43),
        ]
    ),
    "PEM_KEY": _pem("RSA PRIVATE KEY", "pem-key"),
    "PEM_CERT": _pem("CERTIFICATE", "pem-cert"),
    "GCP_SA": json.dumps(
        {
            "type": "service_account",
            "project_id": "acme-prod",
            "private_key_id": _stream("gcp-key-id", "0123456789abcdef", 40),
            "private_key": _GCP_KEY,
            "client_email": "deployer@acme-prod.iam.gserviceaccount.com",
        }
    ),
    "DB_PASSWORD": "Qx7-" + _stream("db-password", _ALNUM, 12),
    "REDIS_PASSWORD": "rd9!" + _stream("redis-password", _ALNUM, 10),
    "AMQP_PASSWORD": "Mq4_" + _stream("amqp-password", _ALNUM, 10),
    "API_TOKEN": _stream("api-token", "0123456789abcdef", 32),
    "RANDOM_TOKEN": _mixed("random-token", 40),
    "BASIC_AUTH": _mixed("basic-auth", 28),
    "WEBHOOK_SECRET": "whsec" + _stream("webhook", _ALNUM, 20),
    "EMAIL_OPS": "ops.oncall@acme-internal.example",
    "EMAIL_DEV": "jane.doe+k8s@acme-corp.example",
}

# What must be absent after masking. Multi-line secrets are checked through their body
# lines, so a partially masked PEM block or service account file still fails the test.
NEEDLES: dict[str, list[str]] = {name: [value] for name, value in FAKES.items()}
for _name in ("PEM_KEY", "PEM_CERT"):
    NEEDLES[_name] = FAKES[_name].split("\n")[1:-1]
NEEDLES["GCP_SA"] = [*_GCP_KEY.split("\n")[1:-1], "deployer@acme-prod.iam.gserviceaccount.com"]
