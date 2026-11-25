from ape.core.db import SystemConnection, TenantConnection


async def current_org_id(tc: TenantConnection) -> str | None:
    """The tenant the database believes this transaction belongs to."""
    cur = await tc.conn.execute("select app.current_org_id() as org_id")
    row = await cur.fetchone()
    return None if row is None else row["org_id"]


async def ping(sc: SystemConnection) -> None:
    await sc.conn.execute("select 1")
