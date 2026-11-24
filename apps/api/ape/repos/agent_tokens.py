from datetime import datetime
from typing import Any
from uuid import UUID

from ape.core.db import SystemConnection, TenantConnection

# token_hash is deliberately absent: it never leaves the database.
_COLUMNS = "id, cluster_id, prefix, created_by, expires_at, revoked_at, last_used_at, created_at"


async def create(
    tc: TenantConnection,
    *,
    cluster_id: UUID,
    prefix: str,
    digest: bytes,
    created_by: str,
    expires_at: datetime | None,
) -> dict[str, Any]:
    cur = await tc.conn.execute(
        "insert into agent_tokens (org_id, cluster_id, prefix, token_hash, created_by, expires_at) "
        f"values (%s, %s, %s, %s, %s, %s) returning {_COLUMNS}",
        (tc.org_id, cluster_id, prefix, digest, created_by, expires_at),
    )
    row = await cur.fetchone()
    assert row is not None  # noqa: S101
    return row


async def list_for_cluster(tc: TenantConnection, cluster_id: UUID) -> list[dict[str, Any]]:
    cur = await tc.conn.execute(
        f"select {_COLUMNS} from agent_tokens where org_id = %s and cluster_id = %s "
        "order by created_at desc",
        (tc.org_id, cluster_id),
    )
    return await cur.fetchall()


async def revoke(
    tc: TenantConnection, *, cluster_id: UUID, token_id: UUID
) -> dict[str, Any] | None:
    """Revokes the token (idempotent). Returns None if it does not exist in this tenant."""
    cur = await tc.conn.execute(
        "update agent_tokens set revoked_at = coalesce(revoked_at, now()) "
        f"where org_id = %s and cluster_id = %s and id = %s returning {_COLUMNS}",
        (tc.org_id, cluster_id, token_id),
    )
    return await cur.fetchone()


async def resolve(sc: SystemConnection, digest: bytes) -> tuple[str, UUID] | None:
    """Maps a token digest to (org_id, cluster_id) through the SECURITY DEFINER function.

    This is the only query that crosses tenants, and it does so inside PostgreSQL:
    the caller learns nothing unless it presents the digest of a live token.
    """
    cur = await sc.conn.execute(
        "select org_id, cluster_id from app.resolve_agent_token(%s)", (digest,)
    )
    row = await cur.fetchone()
    return None if row is None else (row["org_id"], row["cluster_id"])
