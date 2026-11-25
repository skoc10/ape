from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ape.core.db import TenantConnection

_COLUMNS = (
    "id, cluster_id, status, triggered_by, score, stats, pr_url, error, created_at, finished_at, "
    "(snapshot_key is not null) as has_snapshot, (report_key is not null) as has_report"
)

# A run that is still queued/collecting after this long lost its agent or its API process.
STALE_AFTER_MINUTES = 10


async def create(tc: TenantConnection, *, cluster_id: UUID, triggered_by: str) -> dict[str, Any]:
    cur = await tc.conn.execute(
        "insert into audit_runs (org_id, cluster_id, triggered_by, status) "
        f"values (%s, %s, %s, 'collecting') returning {_COLUMNS}",
        (tc.org_id, cluster_id, triggered_by),
    )
    row = await cur.fetchone()
    assert row is not None  # noqa: S101
    return row


async def get(tc: TenantConnection, run_id: UUID) -> dict[str, Any] | None:
    cur = await tc.conn.execute(
        f"select {_COLUMNS} from audit_runs where org_id = %s and id = %s", (tc.org_id, run_id)
    )
    return await cur.fetchone()


async def list_for_cluster(
    tc: TenantConnection, cluster_id: UUID, *, limit: int = 20
) -> list[dict[str, Any]]:
    cur = await tc.conn.execute(
        f"select {_COLUMNS} from audit_runs where org_id = %s and cluster_id = %s "
        "order by created_at desc limit %s",
        (tc.org_id, cluster_id, limit),
    )
    return await cur.fetchall()


async def has_active_collection(tc: TenantConnection, cluster_id: UUID) -> bool:
    cur = await tc.conn.execute(
        "select 1 from audit_runs where org_id = %s and cluster_id = %s "
        "and status in ('queued', 'collecting') limit 1",
        (tc.org_id, cluster_id),
    )
    return await cur.fetchone() is not None


async def fail_stale(tc: TenantConnection, cluster_id: UUID) -> int:
    cur = await tc.conn.execute(
        "update audit_runs set status = 'failed', finished_at = now(), "
        "error = 'collection did not finish (agent or control plane restarted)' "
        "where org_id = %s and cluster_id = %s and status in ('queued', 'collecting') "
        f"and created_at < now() - interval '{STALE_AFTER_MINUTES} minutes'",
        (tc.org_id, cluster_id),
    )
    return cur.rowcount


async def mark_collected(
    tc: TenantConnection, run_id: UUID, *, snapshot_key: str, stats: dict[str, Any]
) -> bool:
    cur = await tc.conn.execute(
        "update audit_runs set status = 'collected', snapshot_key = %s, stats = %s "
        "where org_id = %s and id = %s and status = 'collecting'",
        (snapshot_key, Jsonb(stats), tc.org_id, run_id),
    )
    return cur.rowcount == 1


async def mark_failed(tc: TenantConnection, run_id: UUID, *, error: str) -> bool:
    cur = await tc.conn.execute(
        "update audit_runs set status = 'failed', error = %s, finished_at = now() "
        "where org_id = %s and id = %s and status in ('queued', 'collecting')",
        (error[:500], tc.org_id, run_id),
    )
    return cur.rowcount == 1
