"""Connection pool and the only two ways to get a database connection.

* ``Database.tenant(org_id)`` opens a transaction and pins ``app.org_id`` for its
  duration, so every statement runs under that tenant's RLS policies.
* ``Database.system()`` opens a transaction with no tenant context. RLS then
  hides every application row; it is only useful for the identity lookups and
  the ``app.resolve_agent_token`` function.

``set_config(..., is_local => true)`` scopes the setting to the transaction, so
a pooled connection can never leak one tenant's context into the next request.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

type Conn = AsyncConnection[DictRow]


@dataclass(frozen=True, slots=True)
class TenantConnection:
    """A connection inside a transaction that is pinned to one organization."""

    conn: Conn
    org_id: str


@dataclass(frozen=True, slots=True)
class SystemConnection:
    """A connection inside a transaction without tenant context."""

    conn: Conn


class Database:
    def __init__(self, conninfo: str, *, min_size: int = 1, max_size: int = 10) -> None:
        if not conninfo:
            raise ValueError("DATABASE_URL is not set")
        kwargs: dict[str, Any] = {"row_factory": dict_row}
        self._pool: AsyncConnectionPool[Conn] = AsyncConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            kwargs=kwargs,
            open=False,
        )

    async def open(self) -> None:
        await self._pool.open()

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def tenant(self, org_id: str) -> AsyncIterator[TenantConnection]:
        if not org_id:
            raise ValueError("org_id is required for a tenant transaction")
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute("select set_config('app.org_id', %s, true)", (org_id,))
            yield TenantConnection(conn, org_id)

    @asynccontextmanager
    async def system(self) -> AsyncIterator[SystemConnection]:
        async with self._pool.connection() as conn, conn.transaction():
            yield SystemConnection(conn)
