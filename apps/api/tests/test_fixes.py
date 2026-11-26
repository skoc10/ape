"""Fixes as a download: a zip of patch files that needs no access to any repository."""

import io
import zipfile
from datetime import UTC, datetime
from typing import Any

import psycopg
import yaml
from fastapi.testclient import TestClient

from ape.reports.fixes import build_fix_bundle

from .conftest import Tenant
from .test_github import audit_with_patch, auth_a, auth_b  # noqa: F401 - fixtures
from .test_patcher import PATCH

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def row(
    rule_id: str, kind: str, namespace: str | None, name: str, patch: str | None
) -> dict[str, Any]:
    return {
        "fingerprint": f"{rule_id}-{kind}-{namespace}-{name}",
        "rule_id": rule_id,
        "title": f"Title of {rule_id}",
        "resource_kind": kind,
        "resource_ns": namespace,
        "resource_name": name,
        "patch": patch,
    }


def unzip(archive: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.testzip() is None
        return {name: bundle.read(name).decode() for name in bundle.namelist()}


def test_bundle_has_one_complete_patch_per_resource() -> None:
    rows = [
        row("SEC-001", "Deployment", "shop", "storefront", PATCH),
        # A second finding on the same resource is merged into the same file.
        row(
            "SEC-003",
            "Deployment",
            "shop",
            "storefront",
            "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n"
            "          securityContext:\n            runAsNonRoot: true\n",
        ),
        row("NET-002", "Service", "shop", "gateway", "spec:\n  type: ClusterIP\n"),
        row("RBAC-004", "ClusterRole", None, "ops:reader", "rules: []\n"),
        row("REL-001", "Deployment", "shop", "no-patch", None),
    ]
    bundle = build_fix_bundle(rows, cluster_name="prod-eu", now=NOW)
    files = unzip(bundle.archive)
    assert sorted(files) == [
        "README.md",
        "kustomization.example.yaml",
        "patches/cluster-scoped/clusterrole-ops_reader.yaml",
        "patches/shop/deployment-storefront.yaml",
        "patches/shop/service-gateway.yaml",
    ]
    deployment = yaml.safe_load(files["patches/shop/deployment-storefront.yaml"])
    assert deployment["apiVersion"] == "apps/v1" and deployment["kind"] == "Deployment"
    assert deployment["metadata"] == {"name": "storefront", "namespace": "shop"}
    container = deployment["spec"]["template"]["spec"]["containers"]
    assert container == [
        {
            "name": "web",
            "securityContext": {
                "privileged": False,
                "allowPrivilegeEscalation": False,
                "runAsNonRoot": True,
            },
        }
    ]
    role = yaml.safe_load(files["patches/cluster-scoped/clusterrole-ops_reader.yaml"])
    assert role["metadata"] == {"name": "ops:reader"}
    assert role["apiVersion"] == "rbac.authorization.k8s.io/v1"
    assert yaml.safe_load(files["kustomization.example.yaml"]) == {
        "patches": [
            {"path": "patches/cluster-scoped/clusterrole-ops_reader.yaml"},
            {"path": "patches/shop/deployment-storefront.yaml"},
            {"path": "patches/shop/service-gateway.yaml"},
        ]
    }
    readme = files["README.md"]
    assert "prod-eu (2026-11-26)" in readme and "--dry-run=server" in readme
    assert "SEC-001 Title of SEC-001; SEC-003 Title of SEC-003" in readme
    assert "no-patch" not in readme
    # Same input, same bytes: the archive carries no clock of its own.
    assert build_fix_bundle(rows, cluster_name="prod-eu", now=NOW).archive == bundle.archive


def test_bundle_never_contains_masked_values_or_a_foreign_identity() -> None:
    masked = "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n          env:\n            - name: KEY\n              value: <secret:generic:ab12>\n"
    rows = [
        row("SEC-001", "Deployment", "shop", "leaky", masked),
        row("SEC-002", "Deployment", "shop", "broken", "- not\n- a mapping\n"),
        row("SEC-003", "Deployment", "shop", "syntax", "spec: [unclosed\n"),
        # The patch text claims to be another resource: the audited one wins.
        row(
            "SEC-004",
            "Deployment",
            "shop",
            "web",
            "kind: ClusterRoleBinding\nmetadata:\n  name: cluster-admin\n  namespace: kube-system\n"
            "  labels:\n    fixed: 'yes'\nspec:\n  replicas: 2\n",
        ),
        # Names are not trusted as paths.
        row("NET-002", "Service", "../../etc", "..\\evil/name", "spec:\n  type: ClusterIP\n"),
    ]
    bundle = build_fix_bundle(rows, cluster_name="evil | <b>@x</b> [l](http://x)", now=NOW)
    files = unzip(bundle.archive)
    assert "<secret:" not in "".join(files.values())
    assert {reason for _, reason in bundle.manual} == {
        "the patch contains a masked value",
        "the patch is not a YAML mapping",
    }
    assert len(bundle.manual) == 3
    web = yaml.safe_load(files["patches/shop/deployment-web.yaml"])
    assert web["kind"] == "Deployment" and web["apiVersion"] == "apps/v1"
    assert web["metadata"] == {"name": "web", "namespace": "shop", "labels": {"fixed": "yes"}}
    assert web["spec"] == {"replicas": 2}
    for name in files:
        assert ".." not in name and "\\" not in name and not name.startswith("/")
    assert "patches/etc/service-evil_name.yaml" in files
    readme = files["README.md"]
    assert "<b>" not in readme and "[l](http" not in readme and "## Not included" in readme


def test_download_endpoint_serves_the_zip_to_the_tenant_only(
    client: TestClient,
    admin_db: psycopg.Connection[Any],
    tenant_a: Tenant,
    auth_a: dict[str, str],  # noqa: F811
    auth_b: dict[str, str],  # noqa: F811
) -> None:
    run_id = audit_with_patch(admin_db, tenant_a)
    response = client.get(f"/v1/audits/{run_id}/fixes", headers=auth_a)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="audit-fixes-{run_id}.zip"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    files = unzip(response.content)
    assert "patches/shop/deployment-storefront.yaml" in files
    assert "patches/shop/service-from-helm.yaml" in files
    # Another tenant cannot tell that the audit exists.
    assert client.get(f"/v1/audits/{run_id}/fixes", headers=auth_b).status_code == 404
    assert client.get(f"/v1/audits/{run_id}/fixes").status_code == 401


def test_download_endpoint_preconditions(
    client: TestClient,
    admin_db: psycopg.Connection[Any],
    tenant_a: Tenant,
    tenant_b: Tenant,
    auth_a: dict[str, str],  # noqa: F811
    auth_b: dict[str, str],  # noqa: F811
) -> None:
    running = audit_with_patch(admin_db, tenant_a, status="analyzing")
    assert client.get(f"/v1/audits/{running}/fixes", headers=auth_a).status_code == 409
    empty = audit_with_patch(admin_db, tenant_b, patch=None)
    response = client.get(f"/v1/audits/{empty}/fixes", headers=auth_b)
    assert response.status_code == 404 and "no patches" in response.json()["detail"]
