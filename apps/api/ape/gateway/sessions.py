"""In-memory registry of connected agents (single api instance in the MVP)."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import WebSocket
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from ape.gateway import protocol
from ape.observability.metrics import Metrics

# Application close codes (4000-4999 are reserved for applications).
CLOSE_PROTOCOL_ERROR = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_TIMEOUT = 4408
CLOSE_REPLACED = 4409


@dataclass(eq=False)
class AgentSession:
    """One authenticated agent connection.

    ``org_id`` and ``cluster_id`` come from the token at connect time and never
    change: nothing an agent sends can move the session to another tenant.
    """

    session_id: str
    org_id: str
    cluster_id: UUID
    token_digest: bytes
    websocket: WebSocket
    metrics: Metrics
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: BaseModel) -> None:
        frame = protocol.encode(payload)
        async with self._send_lock:
            await self.websocket.send_text(frame)
        message_type = next(t for t, m in protocol.PAYLOADS.items() if isinstance(payload, m))
        self.metrics.agent_messages.labels(message_type.value, "out").inc()

    async def close(self, code: int) -> None:
        async with self._send_lock:
            if self.websocket.application_state is WebSocketState.CONNECTED:
                await self.websocket.close(code=code)


class SessionRegistry:
    """At most one live session per cluster; a newer connection replaces the older one."""

    def __init__(self) -> None:
        self._by_cluster: dict[UUID, AgentSession] = {}

    def get(self, cluster_id: UUID) -> AgentSession | None:
        return self._by_cluster.get(cluster_id)

    async def add(self, session: AgentSession) -> None:
        previous = self._by_cluster.get(session.cluster_id)
        self._by_cluster[session.cluster_id] = session
        if previous is not None:
            await previous.close(CLOSE_REPLACED)

    def remove(self, session: AgentSession) -> bool:
        """Removes the session if it is still the current one for its cluster."""
        if self._by_cluster.get(session.cluster_id) is session:
            del self._by_cluster[session.cluster_id]
            return True
        return False
