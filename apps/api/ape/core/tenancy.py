"""Who is calling and for which tenant.

A request is allowed only if the JWT is valid AND the user is still a member of
the organization named in the token. Membership is re-checked against the
``member`` table and cached for a short time, so removing a member takes effect
within ``ttl`` seconds even though the token itself lives for 15 minutes.
"""

import time
from dataclasses import dataclass

from ape.core.db import Database
from ape.repos import membership


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    org_id: str
    role: str


class MembershipCache:
    """Short-lived cache of positive membership lookups. Negative results are never cached."""

    def __init__(self, db: Database, *, ttl: float = 60.0, max_entries: int = 10_000) -> None:
        self._db = db
        self._ttl = ttl
        self._max_entries = max_entries
        self._entries: dict[tuple[str, str], tuple[str, float]] = {}

    async def role(self, user_id: str, org_id: str) -> str | None:
        now = time.monotonic()
        cached = self._entries.get((user_id, org_id))
        if cached is not None and cached[1] > now:
            return cached[0]
        async with self._db.system() as sc:
            role = await membership.get_role(sc, user_id=user_id, org_id=org_id)
        if role is None:
            self._entries.pop((user_id, org_id), None)
            return None
        if len(self._entries) >= self._max_entries:
            self._entries.clear()
        self._entries[(user_id, org_id)] = (role, now + self._ttl)
        return role
