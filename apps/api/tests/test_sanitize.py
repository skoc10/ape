"""Masking, first two detectors (task 2.6). The full corpus test arrives with 3.2."""

import json
from typing import Any

import pytest

from ape.sanitize import Masker, derive_tenant_key

MASTER = b"test-master-key-0123456789abcdef"

PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAfakefakefakefakefakefakefakefakefakefakefakefakefake\n"
    "-----END RSA PRIVATE KEY-----"
)

SECRETS = [
    "hunter2-db-password",
    "tok3n-value-123",
    "AKIAFAKEFAKEFAKE0001",
    "s3cr3t-annotation",
    "nested-map-secret",
    "list-secret-a",
    "MIIEowIBAAKCAQEAfake",
    "client-secret-xyz",
]


def pod() -> dict[str, Any]:
    return {
        "kind": "Pod",
        "metadata": {
            "name": "web",
            "namespace": "prod",
            "annotations": {"example.com/api-token": "s3cr3t-annotation", "team": "payments"},
        },
        "spec": {
            "serviceAccountName": "web",
            "automountServiceAccountToken": True,
            "imagePullSecrets": [{"name": "registry-creds"}],
            "volumes": [
                {"name": "tls", "secret": {"secretName": "web-tls"}},
                {
                    "name": "sa",
                    "projected": {"sources": [{"serviceAccountToken": {"path": "token"}}]},
                },
            ],
            "containers": [
                {
                    "name": "app",
                    "image": "ghcr.io/acme/app:1.2.3",
                    "env": [
                        {"name": "DB_PASSWORD", "value": "hunter2-db-password"},
                        {"name": "LOG_LEVEL", "value": "debug"},
                        {"name": "AWS_ACCESS_KEY_ID", "value": "AKIAFAKEFAKEFAKE0001"},
                        {
                            "name": "API_TOKEN",
                            "valueFrom": {"secretKeyRef": {"name": "app-secrets", "key": "token"}},
                        },
                    ],
                    "args": ["--config=/etc/app.yaml"],
                }
            ],
        },
        "data": {
            "password": "tok3n-value-123",
            "credentials": {"user": "admin", "pass": "nested-map-secret"},
            "tokens": ["list-secret-a"],
            "clientSecret": "client-secret-xyz",
            "tls.crt": f"prefix {PEM} suffix",
        },
    }


@pytest.fixture
def masker() -> Masker:
    return Masker(derive_tenant_key(MASTER, "org_a"))


def test_no_known_secret_survives(masker: Masker) -> None:
    output = json.dumps(masker.mask(pod()))
    for secret in SECRETS:
        assert secret not in output
    assert "BEGIN RSA PRIVATE KEY" not in output


def test_structure_needed_for_analysis_is_kept(masker: Masker) -> None:
    masked = masker.mask(pod())
    spec = masked["spec"]
    assert spec["imagePullSecrets"] == [{"name": "registry-creds"}]
    assert spec["volumes"][0] == {"name": "tls", "secret": {"secretName": "web-tls"}}
    assert spec["volumes"][1]["projected"]["sources"][0] == {
        "serviceAccountToken": {"path": "token"}
    }
    assert spec["automountServiceAccountToken"] is True
    container = spec["containers"][0]
    assert container["image"] == "ghcr.io/acme/app:1.2.3"
    assert container["env"][1] == {"name": "LOG_LEVEL", "value": "debug"}
    assert container["env"][3]["valueFrom"] == {
        "secretKeyRef": {"name": "app-secrets", "key": "token"}
    }
    assert [e["name"] for e in container["env"]] == [
        "DB_PASSWORD",
        "LOG_LEVEL",
        "AWS_ACCESS_KEY_ID",
        "API_TOKEN",
    ]
    assert masked["metadata"]["annotations"]["team"] == "payments"
    assert masked["data"]["credentials"]["user"] != "admin"  # everything under a sensitive key goes


def test_placeholders_are_typed_and_stable(masker: Masker) -> None:
    masked = masker.mask(pod())
    value = masked["spec"]["containers"][0]["env"][0]["value"]
    assert value.startswith("<secret:key_name:") and value.endswith(">")
    assert len(value) == len("<secret:key_name:0000>")
    assert masked["data"]["tls.crt"].startswith("prefix <secret:pem:")
    assert masked["data"]["tls.crt"].endswith("> suffix")
    assert masker.redactions["known_format"] == 1
    assert masker.redactions["key_name"] >= 6


def test_masking_is_deterministic_and_idempotent(masker: Masker) -> None:
    first = masker.mask(pod())
    again = Masker(derive_tenant_key(MASTER, "org_a")).mask(pod())
    assert first == again
    second_pass = Masker(derive_tenant_key(MASTER, "org_a"))
    assert second_pass.mask(first) == first
    assert sum(second_pass.redactions.values()) == 0


def test_same_secret_same_placeholder_but_not_across_tenants() -> None:
    doc = {
        "a": {"password": "shared-secret"},
        "b": {"token": "shared-secret"},
        "c": {"token": "other"},
    }
    tenant_a = Masker(derive_tenant_key(MASTER, "org_a")).mask(doc)
    tenant_b = Masker(derive_tenant_key(MASTER, "org_b")).mask(doc)
    assert tenant_a["a"]["password"] == tenant_a["b"]["token"]
    assert tenant_a["a"]["password"] != tenant_a["c"]["token"]
    assert tenant_a["a"]["password"] != tenant_b["a"]["password"]


def test_agent_redactions_pass_through(masker: Masker) -> None:
    doc = {"env": [{"name": "DB_PASSWORD", "value": "<redacted:len=12>"}]}
    assert masker.mask(doc) == doc


def test_input_is_not_modified(masker: Masker) -> None:
    original = pod()
    snapshot = json.dumps(original, sort_keys=True)
    masker.mask(original)
    assert json.dumps(original, sort_keys=True) == snapshot


def test_short_keys_are_rejected() -> None:
    with pytest.raises(ValueError):
        Masker(b"short")


def test_structural_names_do_not_exempt_plain_strings(masker: Masker) -> None:
    """Week 2 review: an annotation called "secret" slipped through the allowlist."""
    obj = {
        "metadata": {"annotations": {"secret": "annot-leak-a", "secrets": "annot-leak-b"}},
        "spec": {
            "volumes": [{"name": "tls", "secret": {"secretName": "web-tls"}}],
            "containers": [{"env": [{"name": "SECRET", "value": "env-leak-c"}]}],
        },
    }
    out = json.dumps(masker.mask(obj))
    for leak in ("annot-leak-a", "annot-leak-b", "env-leak-c"):
        assert leak not in out
    assert '"secretName": "web-tls"' in out


def test_credential_headers_are_masked(masker: Masker) -> None:
    headers = [
        {"name": "Authorization", "value": "Bearer header-leak-a"},
        {"name": "Cookie", "value": "session=header-leak-b"},
        {"name": "X-Request-Source", "value": "kubelet"},
    ]
    out = json.dumps(masker.mask({"httpGet": {"httpHeaders": headers}}))
    assert "header-leak" not in out
    assert "kubelet" in out
