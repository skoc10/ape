"""GitHub connection and pull requests (task 4.4), against a fake GitHub."""

import base64
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from ape.core.config import Settings
from ape.github.pull_request import plan_pull_request
from ape.server import create_app

from .conftest import SigningKey, Tenant, TokenFactory, bearer
from .test_patcher import DEPLOYMENT, PATCH

INSTALLATION = 4242
REPO = "acme/infra"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()


class FakeGitHub:
    """Just enough of github.com and api.github.com, recording every write."""

    def __init__(self) -> None:
        self.user_installations = [INSTALLATION]
        self.files = {
            "k8s/storefront.yaml": DEPLOYMENT,
            "k8s/README.md": "# not yaml",
            "other/ignored.yaml": DEPLOYMENT.replace("storefront", "elsewhere"),
        }
        self.requests: list[tuple[str, str]] = []
        self.created: dict[str, Any] = {}
        self.app_jwts: list[str] = []
        self.fail_pulls = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        auth = request.headers.get("authorization", "")
        if path == "/login/oauth/access_token":
            form = dict(p.split("=") for p in request.content.decode().split("&"))
            ok = form.get("code") == "good-code" and form.get("client_secret") == "client-secret"
            return httpx.Response(
                200,
                json={"access_token": "user-token"} if ok else {"error": "bad_verification_code"},
            )
        if path == "/user/installations":
            assert auth == "Bearer user-token"
            return httpx.Response(
                200, json={"installations": [{"id": i} for i in self.user_installations]}
            )
        if path == f"/app/installations/{INSTALLATION}/access_tokens":
            self.app_jwts.append(auth.removeprefix("Bearer "))
            return httpx.Response(201, json={"token": "installation-token"})
        if path.startswith("/app/installations/"):
            return httpx.Response(404, json={"message": "Not Found"})
        assert auth == "Bearer installation-token", f"unauthenticated call to {path}"
        if path == "/installation/repositories":
            repos = [{"full_name": REPO, "default_branch": "main", "private": True}]
            return httpx.Response(200, json={"repositories": repos})
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == f"/repos/{REPO}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if path == f"/repos/{REPO}/git/trees/basesha":
            tree = [
                {"path": p, "type": "blob", "sha": f"blob-{i}", "size": len(c)}
                for i, (p, c) in enumerate(self.files.items())
            ]
            return httpx.Response(200, json={"tree": tree})
        if path.startswith(f"/repos/{REPO}/git/blobs/"):
            content = list(self.files.values())[int(path.rsplit("-", 1)[1])]
            return httpx.Response(
                200, json={"content": base64.b64encode(content.encode()).decode()}
            )
        if path == f"/repos/{REPO}/git/commits/basesha":
            return httpx.Response(200, json={"tree": {"sha": "basetree"}})
        body = json.loads(request.content) if request.content else {}
        if (method, path) == ("POST", f"/repos/{REPO}/git/trees"):
            self.created["tree"] = body
            return httpx.Response(201, json={"sha": "newtree"})
        if (method, path) == ("POST", f"/repos/{REPO}/git/commits"):
            self.created["commit"] = body
            return httpx.Response(201, json={"sha": "newcommit"})
        if (method, path) == ("POST", f"/repos/{REPO}/git/refs"):
            self.created["ref"] = body
            return httpx.Response(201, json={})
        if (method, path) == ("POST", f"/repos/{REPO}/pulls"):
            if self.fail_pulls:
                return httpx.Response(422, json={"message": "Validation Failed: secret-ish detail"})
            self.created["pull"] = body
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/7"})
        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def gh_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "github_app_id": "12345",
            "github_app_slug": "audit-fixes",
            "github_app_private_key": PRIVATE_PEM.replace(
                "\n", "\\n"
            ),  # as it arrives from an env var
            "github_app_client_id": "client-id",
            "github_app_client_secret": "client-secret",
            "github_api_url": "https://api.github.test",
            "github_web_url": "https://github.test",
            "public_web_url": "https://panel.example.com/acme",
        }
    )


@pytest.fixture
def gh_client(
    gh_settings: Settings, signing_key: SigningKey, github: FakeGitHub
) -> Iterator[TestClient]:
    async def fetch_jwks() -> dict[str, Any]:
        return {"keys": [signing_key.jwk]}

    app = create_app(
        gh_settings, jwks_fetcher=fetch_jwks, github_transport=httpx.MockTransport(github.handler)
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_a(make_token: TokenFactory, tenant_a: Tenant) -> dict[str, str]:
    return bearer(make_token(tenant_a.user_id, tenant_a.org_id))


@pytest.fixture
def auth_b(make_token: TokenFactory, tenant_b: Tenant) -> dict[str, str]:
    return bearer(make_token(tenant_b.user_id, tenant_b.org_id))


def state_of(client: TestClient, headers: dict[str, str]) -> str:
    url = client.get("/v1/settings/github", headers=headers).json()["install_url"]
    assert url.startswith("https://github.test/apps/audit-fixes/installations/new?state=")
    return str(url.split("state=")[1])


def connect(client: TestClient, headers: dict[str, str]) -> None:
    body = {
        "installation_id": INSTALLATION,
        "code": "good-code",
        "state": state_of(client, headers),
    }
    assert (
        client.post("/v1/settings/github/installation", json=body, headers=headers).status_code
        == 200
    )
    chosen = client.put(
        "/v1/settings/github/repo", json={"repo": REPO, "manifest_path": "k8s"}, headers=headers
    )
    assert chosen.status_code == 200, chosen.text


def audit_with_patch(
    admin_db: psycopg.Connection[Any],
    tenant: Tenant,
    *,
    patch: str | None = PATCH,
    status: str = "completed",
) -> uuid.UUID:
    cluster_id, run_id = uuid.uuid4(), uuid.uuid4()
    admin_db.execute(
        "insert into clusters (id, org_id, name) values (%s, %s, 'prod-eu')",
        (cluster_id, tenant.org_id),
    )
    admin_db.execute(
        "insert into audit_runs (id, org_id, cluster_id, triggered_by, status, score, stats) "
        "values (%s, %s, %s, %s, %s, 42, %s)",
        (
            run_id,
            tenant.org_id,
            cluster_id,
            tenant.user_id,
            status,
            json.dumps({"summary": "Two issues need attention."}),
        ),
    )
    rows = [
        ("SEC-001", "Privileged container", "Deployment", "shop", "storefront", patch, "a" * 64),
        (
            "NET-002",
            "Service exposed | outside <b>@team</b>",
            "Service",
            "shop",
            "from-helm",
            "spec:\n  type: ClusterIP\n" if patch else None,
            "b" * 64,
        ),
        ("REL-001", "Missing probe", "Deployment", "shop", "no-patch", None, "c" * 64),
    ]
    for rule_id, title, kind, ns, name, finding_patch, fingerprint in rows:
        admin_db.execute(
            "insert into findings (org_id, audit_run_id, rule_id, category, severity, title, resource_kind, "
            "resource_ns, resource_name, evidence, patch, fingerprint) "
            "values (%s, %s, %s, 'security', 'high', %s, %s, %s, %s, '{}', %s, %s)",
            (tenant.org_id, run_id, rule_id, title, kind, ns, name, finding_patch, fingerprint),
        )
    return run_id


# --- connecting -----------------------------------------------------------------------


def test_without_credentials_the_feature_is_off(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    auth = bearer(make_token(tenant_a.user_id, tenant_a.org_id))
    status = client.get("/v1/settings/github", headers=auth).json()
    assert status == {
        "configured": False,
        "connected": False,
        "repo": None,
        "manifest_path": "/",
        "install_url": None,
        "can_manage": True,
    }
    body = {"installation_id": INSTALLATION, "code": "x", "state": "y"}
    assert (
        client.post("/v1/settings/github/installation", json=body, headers=auth).status_code == 503
    )


def test_connect_and_choose_repository(
    gh_client: TestClient, auth_a: dict[str, str], auth_b: dict[str, str], github: FakeGitHub
) -> None:
    connect(gh_client, auth_a)
    status = gh_client.get("/v1/settings/github", headers=auth_a).json()
    assert (status["configured"], status["connected"], status["repo"], status["manifest_path"]) == (
        True,
        True,
        REPO,
        "/k8s",
    )
    repos = gh_client.get("/v1/settings/github/repos", headers=auth_a).json()
    assert repos == [{"full_name": REPO, "default_branch": "main", "private": True}]
    assert (
        gh_client.get("/v1/settings/github", headers=auth_b).json()["connected"] is False
    )  # per tenant
    assert gh_client.get("/v1/settings/github/repos", headers=auth_b).status_code == 409

    assert gh_client.delete("/v1/settings/github", headers=auth_a).json()["connected"] is False


def test_an_installation_the_user_does_not_control_is_rejected(
    gh_client: TestClient, auth_a: dict[str, str], github: FakeGitHub
) -> None:
    # The attacker completes the flow with a valid state but somebody else's installation id.
    github.user_installations = [999]
    body = {
        "installation_id": INSTALLATION,
        "code": "good-code",
        "state": state_of(gh_client, auth_a),
    }
    assert (
        gh_client.post("/v1/settings/github/installation", json=body, headers=auth_a).status_code
        == 403
    )
    assert gh_client.get("/v1/settings/github", headers=auth_a).json()["connected"] is False

    github.user_installations = [INSTALLATION]
    bad_code = {**body, "code": "stolen-or-expired"}
    assert (
        gh_client.post(
            "/v1/settings/github/installation", json=bad_code, headers=auth_a
        ).status_code
        == 403
    )


def test_state_is_bound_to_the_user_and_the_tenant(
    gh_client: TestClient, auth_a: dict[str, str], auth_b: dict[str, str]
) -> None:
    state_for_a = state_of(gh_client, auth_a)
    body = {"installation_id": INSTALLATION, "code": "good-code"}
    for state in (state_for_a, "forged.state.value", state_for_a[:-2] + "xx"):
        resp = gh_client.post(
            "/v1/settings/github/installation", json={**body, "state": state}, headers=auth_b
        )
        assert resp.status_code == 400
    assert gh_client.get("/v1/settings/github", headers=auth_b).json()["connected"] is False


@pytest.mark.parametrize(
    ("repo", "path", "expected"),
    [
        ("someone/else", "k8s", 404),  # not covered by the installation
        ("not a repo", "k8s", 422),
        (REPO, "../../etc", 422),
        (REPO, "k8s/../..", 422),
        (REPO, "k8s\nx", 422),
    ],
)
def test_repository_and_path_are_validated(
    gh_client: TestClient, auth_a: dict[str, str], repo: str, path: str, expected: int
) -> None:
    connect(gh_client, auth_a)
    resp = gh_client.put(
        "/v1/settings/github/repo", json={"repo": repo, "manifest_path": path}, headers=auth_a
    )
    assert resp.status_code == expected


def test_members_cannot_manage_the_connection(
    gh_client: TestClient,
    make_token: TokenFactory,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
) -> None:
    admin_db.execute(
        'update "member" set role = \'member\' where "userId" = %s', (tenant_a.user_id,)
    )
    member = bearer(make_token(tenant_a.user_id, tenant_a.org_id))
    status = gh_client.get("/v1/settings/github", headers=member).json()
    assert status["can_manage"] is False and status["install_url"] is None
    assert gh_client.delete("/v1/settings/github", headers=member).status_code == 403
    assert gh_client.get("/v1/settings/github/repos", headers=member).status_code == 403
    assert gh_client.post(f"/v1/audits/{uuid.uuid4()}/pr", headers=member).status_code == 403


# --- pull request ---------------------------------------------------------------------


def test_pull_request_is_opened_with_patched_manifests(
    gh_client: TestClient,
    auth_a: dict[str, str],
    auth_b: dict[str, str],
    tenant_a: Tenant,
    github: FakeGitHub,
    admin_db: psycopg.Connection[Any],
) -> None:
    connect(gh_client, auth_a)
    run_id = audit_with_patch(admin_db, tenant_a)

    resp = gh_client.post(f"/v1/audits/{run_id}/pr", headers=auth_a)
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"pr_url": f"https://github.com/{REPO}/pull/7", "applied": 1, "manual": 1}

    # One commit on a new branch, containing only the changed file, comments intact.
    files = {entry["path"]: entry["content"] for entry in github.created["tree"]["tree"]}
    assert list(files) == ["k8s/storefront.yaml"]
    patched = files["k8s/storefront.yaml"]
    assert "hostNetwork: false" in patched and "privileged: false" in patched
    assert "# TODO: scale up before Black Friday" in patched and "# the main container" in patched
    assert github.created["tree"]["base_tree"] == "basetree"
    assert github.created["commit"]["parents"] == ["basesha"]
    assert github.created["ref"]["ref"].startswith("refs/heads/audit/")
    pull = github.created["pull"]
    assert pull["base"] == "main" and pull["head"] == github.created["ref"]["ref"].removeprefix(
        "refs/heads/"
    )

    body = pull["body"]
    assert "Nothing was applied to the cluster" in body
    assert "Two issues need attention." in body and "Audit score: **42 / 100**" in body
    assert f"https://panel.example.com/acme/audits/{run_id}" in body
    assert (
        "| SEC-001 Privileged container | Deployment/shop/storefront | k8s/storefront.yaml | exact match"
        in body
    )
    assert "## To apply manually" in body and "from-helm" in body and "type: ClusterIP" in body
    assert "no-patch" not in body  # findings without a patch are not part of the PR
    # Finding text cannot break the table, inject HTML or ping people.
    assert "<b>" not in body and "@team" not in body and "outside &lt;b&gt;" in body

    # The URL is stored and visible through the API; a second PR is refused.
    assert (
        gh_client.get(f"/v1/audits/{run_id}", headers=auth_a).json()["pr_url"]
        == f"https://github.com/{REPO}/pull/7"
    )
    assert gh_client.post(f"/v1/audits/{run_id}/pr", headers=auth_a).status_code == 409
    assert gh_client.post(f"/v1/audits/{run_id}/pr", headers=auth_b).status_code == 404

    # The App authenticated with a short-lived RS256 JWT; other directories were not read.
    import jwt as pyjwt

    claims = pyjwt.decode(github.app_jwts[0], _KEY.public_key(), algorithms=["RS256"])
    assert claims["iss"] == "12345" and claims["exp"] - claims["iat"] <= 600
    blobs_read = [p for _, p in github.requests if "/git/blobs/" in p]
    assert blobs_read == [f"/repos/{REPO}/git/blobs/blob-0"]


@pytest.mark.parametrize(
    ("setup", "expected"),
    [("not_connected", 409), ("not_completed", 409), ("no_patches", 409), ("nothing_matches", 409)],
)
def test_pull_request_preconditions(
    gh_client: TestClient,
    auth_a: dict[str, str],
    tenant_a: Tenant,
    github: FakeGitHub,
    admin_db: psycopg.Connection[Any],
    setup: str,
    expected: int,
) -> None:
    if setup != "not_connected":
        connect(gh_client, auth_a)
    if setup == "nothing_matches":
        github.files = {"k8s/other.yaml": DEPLOYMENT.replace("storefront", "something-else")}
    run_id = audit_with_patch(
        admin_db,
        tenant_a,
        patch=None if setup == "no_patches" else PATCH,
        status="analyzing" if setup == "not_completed" else "completed",
    )
    assert gh_client.post(f"/v1/audits/{run_id}/pr", headers=auth_a).status_code == expected
    assert "pull" not in github.created
    assert gh_client.get(f"/v1/audits/{run_id}", headers=auth_a).json()["pr_url"] is None


def test_github_errors_are_reported_without_echoing_their_body(
    gh_client: TestClient,
    auth_a: dict[str, str],
    tenant_a: Tenant,
    github: FakeGitHub,
    admin_db: psycopg.Connection[Any],
) -> None:
    connect(gh_client, auth_a)
    github.fail_pulls = True
    run_id = audit_with_patch(admin_db, tenant_a)
    resp = gh_client.post(f"/v1/audits/{run_id}/pr", headers=auth_a)
    assert resp.status_code == 502
    assert resp.json() == {"detail": "GitHub refused to open the pull request (HTTP 422)"}
    assert gh_client.get(f"/v1/audits/{run_id}", headers=auth_a).json()["pr_url"] is None


def test_pull_request_description_for_the_three_matching_cases() -> None:
    no_namespace = DEPLOYMENT.replace("  namespace: shop   # production namespace\n", "")
    sources = {
        "exact/storefront.yaml": DEPLOYMENT,
        "fallback/api.yaml": no_namespace.replace("storefront", "api"),
        "eu/worker.yaml": no_namespace.replace("storefront", "worker"),
        "us/worker.yaml": no_namespace.replace("storefront", "worker"),
    }

    def row(name: str) -> dict[str, Any]:
        return {
            "fingerprint": name,
            "rule_id": "SEC-002",
            "title": "Access to the host",
            "resource_kind": "Deployment",
            "resource_ns": "shop",
            "resource_name": name,
            "patch": "spec:\n  template:\n    spec:\n      hostNetwork: false\n",
        }

    plan = plan_pull_request(
        sources,
        [row("storefront"), row("api"), row("worker")],
        cluster_name="prod",
        summary="",
        score=None,
        report_url=None,
    )
    assert sorted(plan.result.files) == ["exact/storefront.yaml", "fallback/api.yaml"]
    assert "| exact/storefront.yaml | exact match (kind, namespace, name) |" in plan.body
    assert "| fallback/api.yaml | fallback: matched by kind and name |" in plan.body
    manual = plan.body.split("## To apply manually")[1]
    assert (
        "Deployment/shop/worker" in manual
        and "ambiguous: 2 manifests" in manual
        and "hostNetwork: false" in manual
    )
    assert plan.commit_message == "Apply Kubernetes audit fixes (2 findings)"
