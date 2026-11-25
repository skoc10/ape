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


# --- week 3 review: gaps in the entropy rule and in free-text values ------------------


def test_single_case_and_hex_keys_under_innocent_names_are_masked(masker: Masker) -> None:
    base36 = "gmxg9edn581u33xtplpft75v2seh60kvj50ce9uv"
    base32 = "W53EFR4EDT2SYWB3WKH5DNSIPZZ5FK2Z9RI19R0W"
    letters = "DXRZJzzzzgEOzdmenCkhvMdgaKjIgxNbenNyjOqw"
    hex_key = "a4c123b1612dd272d1371c17149d4395"
    short = "aZ9kQ2mX7pL4wR8tY1bN5v"  # 22 characters: cannot reach 4.4 bits of entropy
    masked = masker.mask(
        {
            "env": [
                {"name": "SIGNING", "value": base36},
                {"name": "SEED", "value": base32},
                {"name": "PEPPER", "value": letters},
                {"name": "SALT", "value": hex_key},
                {"name": "NONCE", "value": short},
            ],
            "args": [f"--seed=prefix-{base36}"],
        }
    )
    dumped = json.dumps(masked)
    for secret in (base36, base32, letters, hex_key, short):
        assert secret not in dumped
    assert masked["env"][3]["value"].startswith("<secret:hex:")
    assert masker.redactions["entropy"] == 6


def test_digests_revisions_and_words_are_not_mistaken_for_keys(masker: Masker) -> None:
    digest = "ab12" * 16
    commit = "9fceb02d0ae598e95dc970b74767f19372d61af8"
    obj = {
        "metadata": {
            "annotations": {"checksum/config": digest, "example.com/build": f"sha256:{digest}"},
            "labels": {"git-commit": commit},
        },
        "spec": {
            "serviceAccountName": "kube-prometheus-stack-prometheus-operator",
            "containers": [
                {
                    "args": [
                        "--feature-gates=CustomResourceValidationExpressions=true",
                        "--feature-gates=GracefulNodeShutdownBasedOnPodPriority=true",
                        "--tls-cipher-suites=TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",
                        "--nodegroup=ng-general-purpose-20240612093011223400000001",
                    ],
                    "env": [
                        {"name": "GIT_COMMIT", "value": commit},
                        {"name": "BUILD_NUMBER", "value": "20261124153045123456789012345678"},
                    ],
                }
            ],
        },
        "status": {"nodeInfo": {"machineID": "ec2a" * 8}},
        "findings": [{"fingerprint": digest}],
    }
    assert masker.mask(obj) == obj
    assert sum(masker.redactions.values()) == 0


def test_url_password_with_an_at_sign_is_masked_whole(masker: Masker) -> None:
    masked = masker.mask_text("postgres://app:p@ssw0rd!x@db:5432/app")
    assert masked.startswith("postgres://app:<secret:url_password:")
    assert masked.endswith("@db:5432/app")
    assert "ssw0rd" not in masked


def test_quoted_values_are_masked_up_to_the_closing_quote(masker: Masker) -> None:
    line = 'export DB_PASSWORD="correct horse battery" MODE="fast"'
    masked = masker.mask_text(line)
    assert "horse" not in masked and "battery" not in masked
    assert masked.endswith('MODE="fast"')
    blob = masker.mask_text('{"password": "x y", "host": "db"}')
    assert '"x y"' not in blob and '"host": "db"' in blob
    assert masker.mask_text(masked) == masked


def test_value_after_a_sensitive_flag_is_masked(masker: Masker) -> None:
    masked = masker.mask({"args": ["--api-key", "zzz999", "--mode", "fast", "--token", "--dry"]})
    assert masked["args"][0] == "--api-key"
    assert masked["args"][1].startswith("<secret:key_name:")
    assert masked["args"][2:] == ["--mode", "fast", "--token", "--dry"]


def test_registry_auth_pwd_and_presigned_urls(masker: Masker) -> None:
    masked = masker.mask(
        {
            "auths": {"registry.example.com": {"auth": "Y2k6aHVudGVyMg=="}},
            "PGPWD": "hunter2",
            "authorization-mode": "Node,RBAC",
            "url": "https://acct.blob.core.windows.net/c?sv=2024&sig=abcDEF123%2Fxyz%3D&se=2030",
            "command": "mysqldump -u backup -pS3cretPass app",
        }
    )
    dumped = json.dumps(masked)
    for secret in ("Y2k6aHVudGVyMg==", "hunter2", "abcDEF123", "S3cretPass"):
        assert secret not in dumped
    assert masked["authorization-mode"] == "Node,RBAC"
    assert masked["url"].endswith("&se=2030")


def test_text_that_only_starts_like_a_placeholder_is_still_masked(masker: Masker) -> None:
    masked = masker.mask_text("mongodb://app:<secret:hunter2hunter2@db/app")
    assert "hunter2" not in masked
