"""Shared fixtures.

The tests run against the real PostgreSQL from docker compose (`task infra:up`
and `task db:migrate`): tenant isolation is only meaningful with real RLS.
JWTs are signed with a throwaway Ed25519 key whose JWKS is injected into the app.
"""

import base64
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from ape.core.config import Settings
from ape.server import create_app

ISSUER = "http://auth.test"
AUDIENCE = "ape-api"

BACKEND_URL = os.environ.get(
    "DATABASE_URL", "postgres://app_backend:backend-dev-password@127.0.0.1:54320/ape"
)
MIGRATOR_URL = os.environ.get(
    "MIGRATOR_DATABASE_URL", "postgres://app_migrator:migrator-dev-password@127.0.0.1:54320/ape"
)


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private: Ed25519PrivateKey

    @property
    def jwk(self) -> dict[str, str]:
        raw = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "kid": self.kid, "x": x}


def new_signing_key(kid: str | None = None) -> SigningKey:
    return SigningKey(kid or uuid.uuid4().hex, Ed25519PrivateKey.generate())


TokenFactory = Callable[..., str]


@pytest.fixture(scope="session")
def signing_key() -> SigningKey:
    return new_signing_key()


@pytest.fixture(scope="session")
def make_token(signing_key: SigningKey) -> TokenFactory:
    def make(
        sub: str | None = "user",
        org_id: str | None = None,
        *,
        key: SigningKey | None = None,
        expires_in: int = 900,
        **overrides: Any,
    ) -> str:
        key = key or signing_key
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + expires_in,
            "org_id": org_id,
        }
        if sub is not None:
            payload["sub"] = sub
        payload.update(overrides)
        payload = {k: v for k, v in payload.items() if v is not ...}
        return jwt.encode(payload, key.private, algorithm="EdDSA", headers={"kid": key.kid})

    return make


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=BACKEND_URL,
        better_auth_url=ISSUER,
        auth_audience=AUDIENCE,
        membership_cache_ttl_seconds=60,
        s3_endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://localhost:8333"),
        s3_access_key=os.environ.get("S3_ACCESS_KEY", "ape-dev-access-key"),
        s3_secret_key=os.environ.get("S3_SECRET_KEY", "ape-dev-secret-key"),
        masking_key="test-masking-key-0123456789abcdef",
    )


@pytest.fixture
def client(settings: Settings, signing_key: SigningKey) -> Iterator[TestClient]:
    async def fetch_jwks() -> dict[str, Any]:
        return {"keys": [signing_key.jwk]}

    with TestClient(create_app(settings, jwks_fetcher=fetch_jwks)) as test_client:
        yield test_client


@dataclass(frozen=True)
class Tenant:
    org_id: str
    user_id: str


@pytest.fixture
def admin_db() -> Iterator[psycopg.Connection[Any]]:
    """Connection as app_migrator (BYPASSRLS) for arranging and inspecting test data."""
    with psycopg.connect(MIGRATOR_URL, autocommit=True) as conn:
        yield conn


def _create_tenant(conn: psycopg.Connection[Any], label: str) -> Tenant:
    suffix = uuid.uuid4().hex[:12]
    tenant = Tenant(org_id=f"test_org_{label}_{suffix}", user_id=f"test_user_{label}_{suffix}")
    conn.execute(
        'insert into "user" (id, name, email, "emailVerified") values (%s, %s, %s, true)',
        (tenant.user_id, label, f"{tenant.user_id}@api.test"),
    )
    conn.execute(
        'insert into "organization" (id, name, slug, "createdAt") values (%s, %s, %s, now())',
        (tenant.org_id, label, tenant.org_id.replace("_", "-")),
    )
    conn.execute(
        'insert into "member" (id, "organizationId", "userId", role, "createdAt") '
        "values (%s, %s, %s, 'owner', now())",
        (f"m_{suffix}", tenant.org_id, tenant.user_id),
    )
    return tenant


def _drop_tenant(conn: psycopg.Connection[Any], tenant: Tenant) -> None:
    conn.execute('delete from "organization" where id = %s', (tenant.org_id,))
    conn.execute('delete from "user" where id = %s', (tenant.user_id,))


@pytest.fixture
def tenant_a(admin_db: psycopg.Connection[Any]) -> Iterator[Tenant]:
    tenant = _create_tenant(admin_db, "a")
    yield tenant
    _drop_tenant(admin_db, tenant)


@pytest.fixture
def tenant_b(admin_db: psycopg.Connection[Any]) -> Iterator[Tenant]:
    tenant = _create_tenant(admin_db, "b")
    yield tenant
    _drop_tenant(admin_db, tenant)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
