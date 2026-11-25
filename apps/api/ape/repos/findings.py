from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ape.core.db import TenantConnection
from ape.rules import Finding

# "recurring": the same rule fired for the same resource in an earlier run of this cluster.
_RECURRING = (
    "exists (select 1 from findings prev join audit_runs prev_run "
    "on prev_run.id = prev.audit_run_id and prev_run.org_id = prev.org_id "
    "where prev.org_id = f.org_id and prev.fingerprint = f.fingerprint "
    "and prev_run.cluster_id = run.cluster_id and prev_run.created_at < run.created_at) "
    "as recurring"
)

_COLUMNS = (
    "f.id, f.audit_run_id, f.rule_id, f.category, f.severity, f.title, f.resource_kind, "
    "f.resource_ns, f.resource_name, f.evidence, f.explanation, f.remediation, f.patch, "
    f"f.fingerprint, f.status, f.created_at, {_RECURRING}"
)

_SEVERITY_ORDER = (
    "case f.severity when 'critical' then 0 when 'high' then 1 when 'medium' then 2 "
    "when 'low' then 3 else 4 end"
)


async def insert_many(tc: TenantConnection, run_id: UUID, findings: list[Finding]) -> int:
    """Stores the rule engine output for a run. Re-running is idempotent per fingerprint."""
    if not findings:
        return 0
    async with tc.conn.cursor() as cur:
        await cur.executemany(
            "insert into findings (org_id, audit_run_id, rule_id, category, severity, title, "
            "resource_kind, resource_ns, resource_name, evidence, fingerprint) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (audit_run_id, fingerprint) do nothing",
            [
                (
                    tc.org_id,
                    run_id,
                    f.rule_id,
                    f.category,
                    f.severity,
                    f.title,
                    f.resource.kind,
                    f.resource.namespace,
                    f.resource.name,
                    Jsonb(f.evidence),
                    f.fingerprint,
                )
                for f in findings
            ],
        )
        return cur.rowcount


async def list_for_run(
    tc: TenantConnection,
    run_id: UUID,
    *,
    severity: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    cur = await tc.conn.execute(
        f"select {_COLUMNS} from findings f join audit_runs run "
        "on run.id = f.audit_run_id and run.org_id = f.org_id "
        "where f.org_id = %s and f.audit_run_id = %s "
        "and (%s::text is null or f.severity = %s) and (%s::text is null or f.category = %s) "
        f"order by {_SEVERITY_ORDER}, f.rule_id, f.resource_ns nulls first, f.resource_name",
        (tc.org_id, run_id, severity, severity, category, category),
    )
    return await cur.fetchall()


async def set_enrichment(
    tc: TenantConnection,
    run_id: UUID,
    fingerprint: str,
    *,
    severity: str,
    title: str,
    explanation: str,
    remediation: str,
    patch: str | None,
) -> bool:
    cur = await tc.conn.execute(
        "update findings set severity = %s, title = %s, explanation = %s, remediation = %s, "
        "patch = %s "
        "where org_id = %s and audit_run_id = %s and fingerprint = %s",
        (severity, title, explanation, remediation, patch, tc.org_id, run_id, fingerprint),
    )
    return cur.rowcount == 1
