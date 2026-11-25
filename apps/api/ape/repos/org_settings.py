from typing import Any

from psycopg.types.json import Jsonb

from ape.core.db import TenantConnection

_COLUMNS = "branding, github_installation_id, github_repo, manifest_path, updated_at"


async def get(tc: TenantConnection) -> dict[str, Any]:
    """The organization's settings; the row is created on first access if it is missing."""
    cur = await tc.conn.execute(
        f"select {_COLUMNS} from organization_settings where org_id = %s", (tc.org_id,)
    )
    row = await cur.fetchone()
    if row is None:
        cur = await tc.conn.execute(
            "insert into organization_settings (org_id) values (%s) "
            f"on conflict (org_id) do update set org_id = excluded.org_id returning {_COLUMNS}",
            (tc.org_id,),
        )
        row = await cur.fetchone()
    assert row is not None  # noqa: S101
    return row


async def merge_branding(tc: TenantConnection, patch: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merges the patch into branding; a None value removes that key."""
    await get(tc)
    remove = [key for key, value in patch.items() if value is None]
    keep = {key: value for key, value in patch.items() if value is not None}
    cur = await tc.conn.execute(
        "update organization_settings set branding = (branding || %s) - %s::text[], "
        "updated_at = now() "
        f"where org_id = %s returning {_COLUMNS}",
        (Jsonb(keep), remove, tc.org_id),
    )
    row = await cur.fetchone()
    assert row is not None  # noqa: S101
    return row


async def set_github(
    tc: TenantConnection, *, installation_id: int | None, repo: str | None, manifest_path: str
) -> dict[str, Any]:
    await get(tc)
    cur = await tc.conn.execute(
        "update organization_settings set github_installation_id = %s, github_repo = %s, "
        f"manifest_path = %s, updated_at = now() where org_id = %s returning {_COLUMNS}",
        (installation_id, repo, manifest_path, tc.org_id),
    )
    row = await cur.fetchone()
    assert row is not None  # noqa: S101
    return row
