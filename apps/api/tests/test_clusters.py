"""Cluster and agent-token API (task 1.5), including cross-tenant attempts."""

import hashlib
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ape.core.config import Settings
from ape.core.db import Database
from ape.core.tokens import generate_agent_token, hash_agent_token
from ape.repos import agent_tokens

from .conftest import Tenant, TokenFactory, bearer


@pytest.fixture
def auth_a(make_token: TokenFactory, tenant_a: Tenant) -> dict[str, str]:
    return bearer(make_token(tenant_a.user_id, tenant_a.org_id))


@pytest.fixture
def auth_b(make_token: TokenFactory, tenant_b: Tenant) -> dict[str, str]:
    return bearer(make_token(tenant_b.user_id, tenant_b.org_id))


def _create_cluster(
    client: TestClient, headers: dict[str, str], name: str = "prod"
) -> dict[str, Any]:
    resp = client.post("/v1/clusters", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _resolve(settings: Settings, token: str) -> tuple[str, uuid.UUID] | None:
    db = Database(settings.database_url, min_size=1, max_size=1)
    await db.open()
    try:
        async with db.system() as sc:
            return await agent_tokens.resolve(sc, hash_agent_token(token))
    finally:
        await db.close()


# --- clusters ----------------------------------------------------------------------


def test_create_and_list_clusters(client: TestClient, auth_a: dict[str, str]) -> None:
    created = _create_cluster(client, auth_a, "prod-eu")
    assert created["name"] == "prod-eu"
    assert created["status"] == "pending"

    listing = client.get("/v1/clusters", headers=auth_a).json()
    assert [c["id"] for c in listing["items"]] == [created["id"]]
    assert listing["can_manage"] is True
    assert client.get(f"/v1/clusters/{created['id']}", headers=auth_a).json() == created


def test_cluster_endpoints_require_a_token(client: TestClient) -> None:
    assert client.get("/v1/clusters").status_code == 401
    assert client.post("/v1/clusters", json={"name": "x"}).status_code == 401
    assert client.post(f"/v1/clusters/{uuid.uuid4()}/tokens").status_code == 401


@pytest.mark.parametrize(
    "name", ["", "Has Spaces", "UPPER", "-leading", "trailing-", "a" * 64, "semi;colon"]
)
def test_invalid_cluster_names_are_rejected(
    client: TestClient, auth_a: dict[str, str], name: str
) -> None:
    assert client.post("/v1/clusters", json={"name": name}, headers=auth_a).status_code == 422


def test_duplicate_name_is_409_within_a_tenant_but_allowed_across_tenants(
    client: TestClient, auth_a: dict[str, str], auth_b: dict[str, str]
) -> None:
    _create_cluster(client, auth_a, "prod")
    assert client.post("/v1/clusters", json={"name": "prod"}, headers=auth_a).status_code == 409
    _create_cluster(client, auth_b, "prod")


def test_tenants_cannot_see_each_others_clusters(
    client: TestClient, auth_a: dict[str, str], auth_b: dict[str, str]
) -> None:
    cluster_a = _create_cluster(client, auth_a, "a-only")
    assert client.get("/v1/clusters", headers=auth_b).json()["items"] == []
    assert client.get(f"/v1/clusters/{cluster_a['id']}", headers=auth_b).status_code == 404
    assert client.get(f"/v1/clusters/{cluster_a['id']}/tokens", headers=auth_b).status_code == 404
    assert client.post(f"/v1/clusters/{cluster_a['id']}/tokens", headers=auth_b).status_code == 404


def test_members_can_read_but_not_manage(
    client: TestClient,
    make_token: TokenFactory,
    tenant_a: Tenant,
    auth_a: dict[str, str],
    admin_db: psycopg.Connection[Any],
) -> None:
    cluster = _create_cluster(client, auth_a)
    member_id = f"test_user_member_{uuid.uuid4().hex[:8]}"
    admin_db.execute(
        'insert into "user" (id, name, email, "emailVerified") values (%s, %s, %s, true)',
        (member_id, "member", f"{member_id}@api.test"),
    )
    admin_db.execute(
        'insert into "member" (id, "organizationId", "userId", role, "createdAt") '
        "values (%s, %s, %s, 'member', now())",
        (f"m_{member_id}", tenant_a.org_id, member_id),
    )
    try:
        member = bearer(make_token(member_id, tenant_a.org_id))
        listing = client.get("/v1/clusters", headers=member).json()
        assert len(listing["items"]) == 1
        assert listing["can_manage"] is False
        assert client.post("/v1/clusters", json={"name": "nope"}, headers=member).status_code == 403
        assert (
            client.post(f"/v1/clusters/{cluster['id']}/tokens", headers=member).status_code == 403
        )
    finally:
        admin_db.execute('delete from "user" where id = %s', (member_id,))


# --- agent tokens ------------------------------------------------------------------


def test_token_is_shown_once_and_only_its_hash_is_stored(
    client: TestClient, auth_a: dict[str, str], admin_db: psycopg.Connection[Any]
) -> None:
    cluster = _create_cluster(client, auth_a)
    resp = client.post(f"/v1/clusters/{cluster['id']}/tokens", headers=auth_a)
    assert resp.status_code == 201
    created = resp.json()
    token = created["token"]
    assert token.startswith("ape_")
    assert len(token) == 4 + 43
    assert created["prefix"] == token[:12]

    row = admin_db.execute(
        "select row_to_json(t)::text as raw, token_hash from agent_tokens t where id = %s",
        (created["id"],),
    ).fetchone()
    assert row is not None
    raw, token_hash = row
    assert bytes(token_hash) == hashlib.sha256(token.encode()).digest()
    assert token not in raw  # the secret is nowhere in the row
    assert token[12:] not in raw

    listed = client.get(f"/v1/clusters/{cluster['id']}/tokens", headers=auth_a).json()
    assert [t["id"] for t in listed] == [created["id"]]
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]


def test_tokens_are_unique_and_high_entropy() -> None:
    tokens = {generate_agent_token().token for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) == 47 and t[4:].isalnum() for t in tokens)


async def test_token_resolves_to_its_tenant_and_stops_after_revocation(
    client: TestClient, settings: Settings, auth_a: dict[str, str], tenant_a: Tenant
) -> None:
    cluster = _create_cluster(client, auth_a)
    created = client.post(f"/v1/clusters/{cluster['id']}/tokens", headers=auth_a).json()

    assert await _resolve(settings, created["token"]) == (tenant_a.org_id, uuid.UUID(cluster["id"]))
    assert await _resolve(settings, created["token"] + "x") is None

    resp = client.delete(f"/v1/clusters/{cluster['id']}/tokens/{created['id']}", headers=auth_a)
    assert resp.status_code == 200
    assert resp.json()["revoked_at"] is not None
    assert await _resolve(settings, created["token"]) is None

    # Revoking twice is harmless and keeps the original timestamp.
    again = client.delete(f"/v1/clusters/{cluster['id']}/tokens/{created['id']}", headers=auth_a)
    assert again.json()["revoked_at"] == resp.json()["revoked_at"]


async def test_another_tenant_cannot_revoke_or_list_a_token(
    client: TestClient, settings: Settings, auth_a: dict[str, str], auth_b: dict[str, str]
) -> None:
    cluster_a = _create_cluster(client, auth_a)
    cluster_b = _create_cluster(client, auth_b)
    token_a = client.post(f"/v1/clusters/{cluster_a['id']}/tokens", headers=auth_a).json()

    # Directly, and by smuggling A's token id under B's own cluster.
    for cluster_id in (cluster_a["id"], cluster_b["id"]):
        resp = client.delete(f"/v1/clusters/{cluster_id}/tokens/{token_a['id']}", headers=auth_b)
        assert resp.status_code == 404
    assert client.get(f"/v1/clusters/{cluster_b['id']}/tokens", headers=auth_b).json() == []
    assert await _resolve(settings, token_a["token"]) is not None  # still valid


def test_expiring_token(client: TestClient, auth_a: dict[str, str]) -> None:
    cluster = _create_cluster(client, auth_a)
    resp = client.post(
        f"/v1/clusters/{cluster['id']}/tokens", json={"expires_in_days": 30}, headers=auth_a
    )
    assert resp.status_code == 201
    assert resp.json()["expires_at"] is not None
    bad = client.post(
        f"/v1/clusters/{cluster['id']}/tokens", json={"expires_in_days": 0}, headers=auth_a
    )
    assert bad.status_code == 422


def test_token_never_appears_in_logs(client: TestClient, auth_a: dict[str, str]) -> None:
    import io
    import logging

    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    stream = io.StringIO()
    original = handler.setStream(stream)
    try:
        cluster = _create_cluster(client, auth_a)
        token = client.post(f"/v1/clusters/{cluster['id']}/tokens", headers=auth_a).json()["token"]
    finally:
        handler.setStream(original)
    logs = stream.getvalue()
    assert "agent_token_created" in logs
    assert token not in logs
    assert token[4:20] not in logs
