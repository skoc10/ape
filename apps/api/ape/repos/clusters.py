from typing import Any
from uuid import UUID

from psycopg import errors

from ape.core.db import TenantConnection

# How long a cluster stays "connected" without a heartbeat (3 missed 30 s intervals).
STALE_AFTER_SECONDS = 90

# The stored status is only trusted while heartbeats keep arriving. If the API
# process dies it cannot mark its agents offline, so staleness decides instead.
_STATUS = (
    "case when status = 'connected' and "
    f"(last_seen_at is null or last_seen_at < now() - interval '{STALE_AFTER_SECONDS} seconds') "
    "then 'offline' else status end as status"
)
_COLUMNS = f"id, name, {_STATUS}, k8s_version, agent_version, last_seen_at, created_at"


class ClusterNameTaken(Exception):
    pass


async def create(tc: TenantConnection, *, name: str) -> dict[str, Any]:
    try:
        # The nested transaction keeps the request's transaction usable after a conflict.
        async with tc.conn.transaction():
            cur = await tc.conn.execute(
                f"insert into clusters (org_id, name) values (%s, %s) returning {_COLUMNS}",
                (tc.org_id, name),
            )
            row = await cur.fetchone()
    except errors.UniqueViolation as exc:
        raise ClusterNameTaken(name) from exc
    assert row is not None  # noqa: S101
    return row


async def list_all(tc: TenantConnection) -> list[dict[str, Any]]:
    cur = await tc.conn.execute(
        f"select {_COLUMNS} from clusters where org_id = %s order by created_at desc, name",
        (tc.org_id,),
    )
    return await cur.fetchall()


async def get(tc: TenantConnection, cluster_id: UUID) -> dict[str, Any] | None:
    cur = await tc.conn.execute(
        f"select {_COLUMNS} from clusters where org_id = %s and id = %s",
        (tc.org_id, cluster_id),
    )
    return await cur.fetchone()


async def mark_connected(
    tc: TenantConnection, cluster_id: UUID, *, k8s_version: str, agent_version: str
) -> bool:
    cur = await tc.conn.execute(
        "update clusters set status = 'connected', k8s_version = %s, agent_version = %s, "
        "last_seen_at = now() where org_id = %s and id = %s",
        (k8s_version, agent_version, tc.org_id, cluster_id),
    )
    return cur.rowcount == 1


async def touch(tc: TenantConnection, cluster_id: UUID) -> None:
    """Heartbeat: the agent is still there."""
    await tc.conn.execute(
        "update clusters set status = 'connected', last_seen_at = now() "
        "where org_id = %s and id = %s",
        (tc.org_id, cluster_id),
    )


async def mark_offline(tc: TenantConnection, cluster_id: UUID) -> None:
    await tc.conn.execute(
        "update clusters set status = 'offline' "
        "where org_id = %s and id = %s and status = 'connected'",
        (tc.org_id, cluster_id),
    )
