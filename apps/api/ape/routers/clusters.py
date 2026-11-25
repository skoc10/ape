from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ape.core.db import Database
from ape.core.deps import CurrentPrincipal
from ape.core.tenancy import Principal
from ape.core.tokens import generate_agent_token
from ape.gateway.endpoint import revalidate
from ape.repos import agent_tokens, clusters

router = APIRouter(prefix="/v1/clusters", tags=["clusters"])
log = structlog.get_logger(__name__)

MANAGER_ROLES = {"owner", "admin"}


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


Db = Annotated[Database, Depends(_db)]


def _require_manager(principal: Principal) -> None:
    if principal.role not in MANAGER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only owners and admins can manage clusters")


def _not_found() -> HTTPException:
    # Also returned for clusters of other tenants: their existence is not revealed.
    return HTTPException(status.HTTP_404_NOT_FOUND, "cluster not found")


class ClusterIn(BaseModel):
    # DNS label, like a Kubernetes namespace name.
    name: str = Field(pattern=r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class ClusterOut(BaseModel):
    id: UUID
    name: str
    status: str
    k8s_version: str | None
    agent_version: str | None
    last_seen_at: datetime | None
    created_at: datetime


class ClusterList(BaseModel):
    items: list[ClusterOut]
    can_manage: bool


class TokenIn(BaseModel):
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class TokenOut(BaseModel):
    id: UUID
    cluster_id: UUID
    prefix: str
    created_by: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class TokenCreated(TokenOut):
    token: str = Field(description="Shown exactly once. Only its SHA-256 digest is stored.")


@router.get("")
async def list_clusters(principal: CurrentPrincipal, db: Db) -> ClusterList:
    async with db.tenant(principal.org_id) as tc:
        rows = await clusters.list_all(tc)
    return ClusterList(
        items=[ClusterOut.model_validate(r) for r in rows],
        can_manage=principal.role in MANAGER_ROLES,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_cluster(body: ClusterIn, principal: CurrentPrincipal, db: Db) -> ClusterOut:
    _require_manager(principal)
    try:
        async with db.tenant(principal.org_id) as tc:
            row = await clusters.create(tc, name=body.name)
    except clusters.ClusterNameTaken:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a cluster with this name already exists"
        ) from None
    log.info("cluster_created", cluster_id=str(row["id"]))
    return ClusterOut.model_validate(row)


@router.get("/{cluster_id}")
async def get_cluster(cluster_id: UUID, principal: CurrentPrincipal, db: Db) -> ClusterOut:
    async with db.tenant(principal.org_id) as tc:
        row = await clusters.get(tc, cluster_id)
    if row is None:
        raise _not_found()
    return ClusterOut.model_validate(row)


@router.get("/{cluster_id}/tokens")
async def list_tokens(cluster_id: UUID, principal: CurrentPrincipal, db: Db) -> list[TokenOut]:
    async with db.tenant(principal.org_id) as tc:
        if await clusters.get(tc, cluster_id) is None:
            raise _not_found()
        rows = await agent_tokens.list_for_cluster(tc, cluster_id)
    return [TokenOut.model_validate(r) for r in rows]


@router.post("/{cluster_id}/tokens", status_code=status.HTTP_201_CREATED)
async def create_token(
    cluster_id: UUID, principal: CurrentPrincipal, db: Db, body: TokenIn | None = None
) -> TokenCreated:
    _require_manager(principal)
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days)
        if body is not None and body.expires_in_days is not None
        else None
    )
    new = generate_agent_token()
    async with db.tenant(principal.org_id) as tc:
        if await clusters.get(tc, cluster_id) is None:
            raise _not_found()
        row = await agent_tokens.create(
            tc,
            cluster_id=cluster_id,
            prefix=new.prefix,
            digest=new.digest,
            created_by=principal.user_id,
            expires_at=expires_at,
        )
    # Only identifiers are logged; the token itself never reaches a log line.
    log.info("agent_token_created", cluster_id=str(cluster_id), token_id=str(row["id"]))
    return TokenCreated.model_validate({**row, "token": new.token})


@router.delete("/{cluster_id}/tokens/{token_id}")
async def revoke_token(
    request: Request, cluster_id: UUID, token_id: UUID, principal: CurrentPrincipal, db: Db
) -> TokenOut:
    _require_manager(principal)
    async with db.tenant(principal.org_id) as tc:
        row = await agent_tokens.revoke(tc, cluster_id=cluster_id, token_id=token_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token not found")
    log.info("agent_token_revoked", cluster_id=str(cluster_id), token_id=str(token_id))
    # If an agent is connected with this token, drop it now (close code 4401).
    await revalidate(db, request.app.state.agent_sessions, cluster_id)
    return TokenOut.model_validate(row)
