"""Authentication and tenant-context tests (task 1.4)."""

import base64
import json
import time
from typing import Any

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from ape.core.auth import InvalidToken, JWKSCache, JWKSUnavailable, TokenVerifier
from ape.core.config import Settings
from ape.server import create_app

from .conftest import AUDIENCE, ISSUER, SigningKey, Tenant, TokenFactory, bearer, new_signing_key


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


# --- 401: no or bad credentials ------------------------------------------------


def test_no_token_is_401(client: TestClient) -> None:
    resp = client.get("/v1/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header", ["Bearer", "Bearer not-a-jwt", "Basic dXNlcjpwYXNz", "Bearer a.b.c"]
)
def test_malformed_authorization_is_401(client: TestClient, header: str) -> None:
    assert client.get("/v1/me", headers={"Authorization": header}).status_code == 401


def test_expired_token_is_401(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    token = make_token(tenant_a.user_id, tenant_a.org_id, expires_in=-60)
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_token_without_expiry_is_401(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    token = make_token(tenant_a.user_id, tenant_a.org_id, exp=...)
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_wrong_audience_is_401(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    token = make_token(tenant_a.user_id, tenant_a.org_id, aud="some-other-api")
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_wrong_issuer_is_401(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    token = make_token(tenant_a.user_id, tenant_a.org_id, iss="http://evil.test")
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_missing_subject_is_401(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    token = make_token(None, tenant_a.org_id)
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_token_signed_by_unknown_key_is_401(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    token = make_token(tenant_a.user_id, tenant_a.org_id, key=new_signing_key())
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_forged_signature_with_known_kid_is_401(
    client: TestClient, make_token: TokenFactory, signing_key: SigningKey, tenant_a: Tenant
) -> None:
    attacker = new_signing_key(kid=signing_key.kid)
    token = make_token(tenant_a.user_id, tenant_a.org_id, key=attacker)
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_alg_none_is_401(client: TestClient, signing_key: SigningKey, tenant_a: Tenant) -> None:
    now = int(time.time())
    payload = {
        "sub": tenant_a.user_id,
        "org_id": tenant_a.org_id,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 900,
    }
    token = f"{_b64({'alg': 'none', 'kid': signing_key.kid})}.{_b64(payload)}."
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_hs256_signed_with_public_key_is_401(
    client: TestClient, signing_key: SigningKey, tenant_a: Tenant
) -> None:
    # Classic key confusion: HMAC "signed" with the public key bytes.
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": tenant_a.user_id,
            "org_id": tenant_a.org_id,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 900,
        },
        signing_key.jwk["x"],
        algorithm="HS256",
        headers={"kid": signing_key.kid},
    )
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


# --- 403: authenticated but not allowed for this organization ------------------


def test_token_without_active_organization_is_403(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    token = make_token(tenant_a.user_id, None)
    assert client.get("/v1/me", headers=bearer(token)).status_code == 403


def test_non_member_organization_is_403(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    # A validly signed token for an organization the user does not belong to.
    token = make_token(tenant_a.user_id, tenant_b.org_id)
    assert client.get("/v1/me", headers=bearer(token)).status_code == 403


def test_unknown_organization_is_403(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    token = make_token(tenant_a.user_id, "org_that_does_not_exist")
    assert client.get("/v1/me", headers=bearer(token)).status_code == 403


def test_removed_member_is_403_once_the_cache_expires(
    settings: Settings,
    signing_key: SigningKey,
    make_token: TokenFactory,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
) -> None:
    async def fetch_jwks() -> dict[str, Any]:
        return {"keys": [signing_key.jwk]}

    no_cache = settings.model_copy(update={"membership_cache_ttl_seconds": 0.0})
    token = make_token(tenant_a.user_id, tenant_a.org_id)
    with TestClient(create_app(no_cache, jwks_fetcher=fetch_jwks)) as client:
        assert client.get("/v1/me", headers=bearer(token)).status_code == 200
        admin_db.execute('delete from "member" where "userId" = %s', (tenant_a.user_id,))
        # The token is still valid for 15 minutes, but membership is gone.
        assert client.get("/v1/me", headers=bearer(token)).status_code == 403


# --- 200: tenant context reaches PostgreSQL --------------------------------------


def test_valid_token_sets_the_tenant_context_in_postgres(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    for tenant in (tenant_a, tenant_b, tenant_a):
        resp = client.get("/v1/me", headers=bearer(make_token(tenant.user_id, tenant.org_id)))
        assert resp.status_code == 200
        assert resp.json() == {
            "user_id": tenant.user_id,
            "org_id": tenant.org_id,
            "role": "owner",
            "db_org_id": tenant.org_id,
        }


async def test_tenant_context_does_not_leak_through_the_pool(
    settings: Settings, tenant_a: Tenant
) -> None:
    from ape.core.db import Database
    from ape.repos import context

    # A single pooled connection, reused for every transaction below.
    db = Database(settings.database_url, min_size=1, max_size=1)
    await db.open()
    try:
        async with db.tenant(tenant_a.org_id) as tc:
            assert await context.current_org_id(tc) == tenant_a.org_id
        async with db.system() as sc:
            cur = await sc.conn.execute(
                "select app.current_org_id() as org_id, count(*) as n from clusters"
            )
            row = await cur.fetchone()
            assert row == {"org_id": None, "n": 0}
        with pytest.raises(ValueError):
            async with db.tenant(""):
                pass
    finally:
        await db.close()


# --- JWKS cache ------------------------------------------------------------------


async def test_jwks_cache_refetches_for_a_rotated_key_but_is_rate_limited() -> None:
    old, new = new_signing_key(), new_signing_key()
    served = [old]
    calls = 0

    async def fetch() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"keys": [k.jwk for k in served]}

    cache = JWKSCache(fetch, min_refresh_interval=3600)
    assert (await cache.get(old.kid)).key_id == old.kid
    assert calls == 1
    await cache.get(old.kid)
    assert calls == 1  # served from cache

    served.append(new)
    with pytest.raises(InvalidToken):
        await cache.get(
            new.kid
        )  # refresh not allowed yet: unknown kids cannot hammer the auth server
    assert calls == 1

    cache = JWKSCache(fetch, min_refresh_interval=0)
    assert (await cache.get(new.kid)).key_id == new.kid


async def test_jwks_outage_is_503_not_401(make_token: TokenFactory) -> None:
    async def fetch() -> dict[str, Any]:
        raise RuntimeError("auth server is down")

    verifier = TokenVerifier(JWKSCache(fetch), issuer=ISSUER, audience=AUDIENCE)
    with pytest.raises(JWKSUnavailable):
        await verifier.verify(make_token("user", "org"))
