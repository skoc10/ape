from typing import Any
from uuid import UUID

from psycopg import errors

from ape.core.db import TenantConnection

_COLUMNS = "id, name, status, k8s_version, agent_version, last_seen_at, created_at"


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
