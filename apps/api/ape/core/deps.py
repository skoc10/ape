"""FastAPI dependencies: authentication, authorization and tenant-scoped connections."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ape.core.auth import InvalidToken, JWKSUnavailable, TokenVerifier
from ape.core.db import Database, TenantConnection
from ape.core.tenancy import MembershipCache, Principal

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    # One generic message for every failure: callers learn nothing about why.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise _unauthorized()
    verifier: TokenVerifier = request.app.state.verifier
    memberships: MembershipCache = request.app.state.memberships
    try:
        claims = await verifier.verify(credentials.credentials)
    except InvalidToken:
        raise _unauthorized() from None
    except JWKSUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "authentication unavailable"
        ) from None

    if claims.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no active organization")
    role = await memberships.role(claims.user_id, claims.org_id)
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not a member of this organization")
    return Principal(user_id=claims.user_id, org_id=claims.org_id, role=role)


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]


async def tenant_connection(
    request: Request, principal: CurrentPrincipal
) -> AsyncIterator[TenantConnection]:
    """One transaction per request, pinned to the caller's organization."""
    db: Database = request.app.state.db
    async with db.tenant(principal.org_id) as tc:
        yield tc


TenantConn = Annotated[TenantConnection, Depends(tenant_connection)]
