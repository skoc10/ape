from ape.core.db import SystemConnection


async def get_role(sc: SystemConnection, *, user_id: str, org_id: str) -> str | None:
    """Returns the user's role in the organization, or None if not a member."""
    cur = await sc.conn.execute(
        'select role from "member" where "userId" = %s and "organizationId" = %s',
        (user_id, org_id),
    )
    row = await cur.fetchone()
    return None if row is None else str(row["role"])
