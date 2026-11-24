from ape.core.db import SystemConnection


async def get_role(sc: SystemConnection, *, user_id: str, org_id: str) -> str | None:
    """Returns the user's role in the organization, or None if not a member."""
    # app_backend has no access to the identity tables; this SECURITY DEFINER function
    # answers exactly one (user, organization) pair and cannot be used to enumerate.
    cur = await sc.conn.execute("select app.member_role(%s, %s) as role", (user_id, org_id))
    row = await cur.fetchone()
    return None if row is None or row["role"] is None else str(row["role"])
