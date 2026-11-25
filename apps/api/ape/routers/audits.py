from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ape.core.db import Database
from ape.core.deps import CurrentPrincipal
from ape.gateway.endpoint import AuditNotStartable, start_collection
from ape.gateway.sessions import SessionRegistry
from ape.repos import audit_runs, clusters, findings

router = APIRouter(prefix="/v1", tags=["audits"])
log = structlog.get_logger(__name__)

MANAGER_ROLES = {"owner", "admin"}


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


Db = Annotated[Database, Depends(_db)]


class AuditRunOut(BaseModel):
    id: UUID
    cluster_id: UUID
    status: str
    triggered_by: str
    score: int | None
    stats: dict[str, Any]
    pr_url: str | None
    error: str | None
    has_snapshot: bool
    has_report: bool
    created_at: datetime
    finished_at: datetime | None


@router.post("/clusters/{cluster_id}/audits", status_code=status.HTTP_201_CREATED)
async def start_audit(
    request: Request, cluster_id: UUID, principal: CurrentPrincipal, db: Db
) -> AuditRunOut:
    if principal.role not in MANAGER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only owners and admins can start an audit")
    registry: SessionRegistry = request.app.state.agent_sessions

    async with db.tenant(principal.org_id) as tc:
        if await clusters.get(tc, cluster_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "cluster not found")
        # The cluster was just read under this tenant, so its session belongs to this tenant.
        session = registry.get(cluster_id)
        if session is None or session.org_id != principal.org_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "the agent is not connected")
        await audit_runs.fail_stale(tc, cluster_id)
        if session.collection is not None or await audit_runs.has_active_collection(tc, cluster_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "a collection is already running for this cluster"
            )
        row = await audit_runs.create(tc, cluster_id=cluster_id, triggered_by=principal.user_id)

    try:
        await start_collection(request.app.state.settings, session, row["id"])
    except AuditNotStartable as exc:
        async with db.tenant(principal.org_id) as tc:
            await audit_runs.mark_failed(tc, row["id"], error=str(exc))
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    log.info("audit_started", run_id=str(row["id"]), cluster_id=str(cluster_id))
    return AuditRunOut.model_validate(row)


@router.get("/clusters/{cluster_id}/audits")
async def list_audits(cluster_id: UUID, principal: CurrentPrincipal, db: Db) -> list[AuditRunOut]:
    async with db.tenant(principal.org_id) as tc:
        if await clusters.get(tc, cluster_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "cluster not found")
        rows = await audit_runs.list_for_cluster(tc, cluster_id)
    return [AuditRunOut.model_validate(r) for r in rows]


@router.get("/audits/{run_id}")
async def get_audit(run_id: UUID, principal: CurrentPrincipal, db: Db) -> AuditRunOut:
    async with db.tenant(principal.org_id) as tc:
        row = await audit_runs.get(tc, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audit not found")
    return AuditRunOut.model_validate(row)


class FindingOut(BaseModel):
    id: UUID
    rule_id: str
    category: str
    severity: str
    title: str
    resource_kind: str
    resource_ns: str | None
    resource_name: str
    evidence: dict[str, Any]
    explanation: str | None
    remediation: str | None
    patch: str | None
    fingerprint: str
    status: str
    # The same rule fired for the same resource in an earlier audit of this cluster.
    recurring: bool


Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal["security", "rbac", "network", "cost", "reliability"]


@router.get("/audits/{run_id}/findings")
async def list_findings(
    run_id: UUID,
    principal: CurrentPrincipal,
    db: Db,
    severity: Severity | None = None,
    category: Category | None = None,
) -> list[FindingOut]:
    async with db.tenant(principal.org_id) as tc:
        if await audit_runs.get(tc, run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "audit not found")
        rows = await findings.list_for_run(tc, run_id, severity=severity, category=category)
    return [FindingOut.model_validate(r) for r in rows]
