from typing import Any

from psycopg.types.json import Jsonb

from ape.core.db import TenantConnection

_COLUMNS = (
    "branding, github_installation_id, github_repo, manifest_path, github_allowed_repos, updated_at"
)


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


async def set_github_installation(
    tc: TenantConnection, *, installation_id: int | None, allowed_repos: list[str] | None
) -> dict[str, Any]:
    """Connects (or, with None, disconnects) an installation. The chosen repository is
    reset, and ``allowed_repos`` replaces the list of repositories that may be chosen."""
    await get(tc)
    cur = await tc.conn.execute(
        "update organization_settings set github_installation_id = %s, github_repo = null, "
        "manifest_path = '/', github_allowed_repos = %s, updated_at = now() "
        f"where org_id = %s returning {_COLUMNS}",
        (installation_id, allowed_repos, tc.org_id),
    )
    row = await cur.fetchone()
    assert row is not None  # noqa: S101
    return row


async def set_github_repo(tc: TenantConnection, *, repo: str, manifest_path: str) -> dict[str, Any]:
    await get(tc)
    cur = await tc.conn.execute(
        "update organization_settings set github_repo = %s, manifest_path = %s, "
        f"updated_at = now() where org_id = %s returning {_COLUMNS}",
        (repo, manifest_path, tc.org_id),
    )
    row = await cur.fetchone()
    assert row is not None  # noqa: S101
    return row
