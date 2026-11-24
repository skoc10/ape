"""JWT verification against the Better Auth JWKS endpoint."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWK, PyJWKSet

# Better Auth signs with Ed25519. The algorithm is pinned here and never taken
# from the token header, which rules out "alg: none" and key-confusion attacks.
ALGORITHMS = ["EdDSA"]

JWKSFetcher = Callable[[], Awaitable[dict[str, Any]]]


class InvalidToken(Exception):
    """The token is missing, malformed, expired or not signed by a known key."""


class JWKSUnavailable(Exception):
    """The signing keys could not be loaded."""


@dataclass(frozen=True, slots=True)
class Claims:
    user_id: str
    org_id: str | None


def http_jwks_fetcher(client: httpx.AsyncClient, url: str) -> JWKSFetcher:
    async def fetch() -> dict[str, Any]:
        response = await client.get(url, timeout=5.0)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    return fetch


class JWKSCache:
    """Caches signing keys by ``kid``; refetches (rate limited) on an unknown ``kid``."""

    def __init__(self, fetch: JWKSFetcher, *, min_refresh_interval: float = 10.0) -> None:
        self._fetch = fetch
        self._min_refresh_interval = min_refresh_interval
        self._keys: dict[str, PyJWK] = {}
        self._last_refresh = float("-inf")
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> PyJWK:
        key = self._keys.get(kid)
        if key is not None:
            return key
        async with self._lock:
            if kid not in self._keys and self._refresh_allowed():
                await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise InvalidToken("unknown signing key")
        return key

    def _refresh_allowed(self) -> bool:
        return time.monotonic() - self._last_refresh >= self._min_refresh_interval

    async def _refresh(self) -> None:
        self._last_refresh = time.monotonic()
        try:
            jwks = PyJWKSet.from_dict(await self._fetch())
        except Exception as exc:
            if self._keys:
                return  # keep serving with the keys we already trust
            raise JWKSUnavailable("could not load signing keys") from exc
        self._keys = {k.key_id: k for k in jwks.keys if k.key_id}


class TokenVerifier:
    def __init__(self, jwks: JWKSCache, *, issuer: str, audience: str) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience

    async def verify(self, token: str) -> Claims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidToken("malformed token") from exc
        kid = header.get("kid")
        if header.get("alg") not in ALGORITHMS or not isinstance(kid, str):
            raise InvalidToken("unsupported token")
        key = await self._jwks.get(kid)
        try:
            payload = jwt.decode(
                token,
                key=key.key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=5,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken("invalid token") from exc
        sub = payload.get("sub")
        org_id = payload.get("org_id")
        if not isinstance(sub, str) or not sub:
            raise InvalidToken("invalid subject")
        if org_id is not None and (not isinstance(org_id, str) or not org_id):
            raise InvalidToken("invalid organization claim")
        return Claims(user_id=sub, org_id=org_id)
