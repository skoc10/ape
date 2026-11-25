"""Agent WebSocket gateway: ``/v1/agent``.

The agent always dials out to us. It authenticates with a bearer token, which
binds the connection to one (organization, cluster) pair for its whole life.
"""

import asyncio
from uuid import UUID

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ape.core.config import Settings
from ape.core.db import Database
from ape.core.tokens import TOKEN_PREFIX, hash_agent_token
from ape.gateway import protocol
from ape.gateway.collection import Collection, CollectionError
from ape.gateway.protocol_gen import (
    CollectChunkPayload,
    CollectDonePayload,
    CollectLimits,
    CollectRequestPayload,
    ErrorPayload,
    HelloAckPayload,
    HelloPayload,
    MessageType,
    PingPayload,
    PongPayload,
)
from ape.gateway.sessions import (
    CLOSE_PROTOCOL_ERROR,
    CLOSE_TIMEOUT,
    CLOSE_UNAUTHORIZED,
    AgentSession,
    SessionRegistry,
)
from ape.observability.metrics import Metrics
from ape.repos import agent_tokens, audit_runs, clusters
from ape.sanitize import Masker, derive_tenant_key
from ape.storage import ObjectStore

router = APIRouter()
log = structlog.get_logger(__name__)

# Three missed heartbeats drop the connection.
MISSED_HEARTBEATS = 3


def _bearer_token(websocket: WebSocket) -> str | None:
    scheme, _, value = websocket.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.startswith(TOKEN_PREFIX) or len(value) > 128:
        return None
    return value


async def _resolve(db: Database, digest: bytes) -> tuple[str, UUID] | None:
    async with db.system() as sc:
        return await agent_tokens.resolve(sc, digest)


async def revalidate(db: Database, registry: SessionRegistry, cluster_id: UUID) -> None:
    """Closes the cluster's live session with 4401 if its token no longer resolves.

    Called right after a token is revoked, and on every heartbeat.
    """
    session = registry.get(cluster_id)
    if session is None:
        return
    if await _resolve(db, session.token_digest) is None:
        log.info("agent_session_revoked", cluster_id=str(cluster_id), session_id=session.session_id)
        await session.close(CLOSE_UNAUTHORIZED)


_cleanups: set[asyncio.Task[None]] = set()


async def _mark_offline(db: Database, org_id: str, cluster_id: UUID) -> None:
    async with db.tenant(org_id) as tc:
        await clusters.mark_offline(tc, cluster_id)


async def _receive(session: AgentSession, seconds: float) -> tuple[MessageType, BaseModel]:
    async with asyncio.timeout(seconds):
        frame = await session.websocket.receive_text()
    envelope, payload = protocol.decode(frame)
    session.metrics.agent_messages.labels(envelope.type.value, "in").inc()
    return envelope.type, payload


@router.websocket("/v1/agent")
async def agent_gateway(websocket: WebSocket) -> None:
    db: Database = websocket.app.state.db
    settings: Settings = websocket.app.state.settings
    registry: SessionRegistry = websocket.app.state.agent_sessions
    metrics: Metrics = websocket.app.state.metrics

    # Accept first so that the agent receives a real close code instead of a bare HTTP 403.
    await websocket.accept()
    token = _bearer_token(websocket)
    digest = hash_agent_token(token) if token else None
    resolved = await _resolve(db, digest) if digest else None
    if resolved is None or digest is None:
        log.info("agent_rejected", reason="invalid_token")
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    org_id, cluster_id = resolved

    session = AgentSession(
        session_id=protocol.new_ulid(),
        org_id=org_id,
        cluster_id=cluster_id,
        token_digest=digest,
        websocket=websocket,
        metrics=metrics,
    )
    bound = log.bind(cluster_id=str(cluster_id), session_id=session.session_id)
    heartbeat = settings.agent_heartbeat_seconds
    registered = False
    try:
        message_type, payload = await _receive(session, settings.agent_hello_timeout_seconds)
        if not isinstance(payload, HelloPayload):
            raise protocol.ProtocolError(f"expected hello, got {message_type.value}")
        async with db.tenant(org_id) as tc:
            await clusters.mark_connected(
                tc, cluster_id, k8s_version=payload.k8s_version, agent_version=payload.agent_version
            )
        await registry.add(session)
        registered = True
        metrics.agent_sessions.inc()
        await session.send(
            HelloAckPayload(
                session_id=session.session_id,
                heartbeat_interval_seconds=heartbeat,
                limits=CollectLimits(
                    max_pods=settings.agent_max_pods,
                    max_compressed_bytes=settings.agent_max_compressed_bytes,
                    max_frame_bytes=protocol.MAX_FRAME_BYTES,
                ),
            )
        )
        bound.info(
            "agent_connected", agent_version=payload.agent_version, node_count=payload.node_count
        )

        while True:
            message_type, payload = await _receive(session, heartbeat * MISSED_HEARTBEATS)
            if isinstance(payload, PingPayload):
                # A heartbeat also re-checks the token, so expiry and revocation always bite.
                if await _resolve(db, digest) is None:
                    await session.close(CLOSE_UNAUTHORIZED)
                    return
                async with db.tenant(org_id) as tc:
                    await clusters.touch(tc, cluster_id)
                await session.send(PongPayload())
            elif isinstance(payload, PongPayload):
                continue
            elif isinstance(payload, CollectChunkPayload | CollectDonePayload | ErrorPayload):
                await _on_collection_message(websocket.app.state.object_store, db, session, payload)
            else:
                # Only the type is logged, never the content.
                bound.warning("agent_message_ignored", type=message_type.value)
    except WebSocketDisconnect:
        bound.info("agent_disconnected")
    except TimeoutError:
        bound.info("agent_timeout")
        await session.close(CLOSE_TIMEOUT)
    except protocol.ProtocolError as exc:
        bound.warning("agent_protocol_error", error=str(exc))
        await session.close(CLOSE_PROTOCOL_ERROR)
    except RuntimeError:
        # The socket was closed from elsewhere (revoked or replaced) while we were using it.
        bound.info("agent_session_closed")
    finally:
        if session.collection is not None:
            await asyncio.shield(_fail_run(db, session, "agent disconnected during the collection"))
        if registered:
            metrics.agent_sessions.dec()
            if registry.remove(session):
                # Shielded: the handler task may be cancelled (shutdown, abrupt disconnect)
                # and the cluster must still be marked offline.
                cleanup = asyncio.create_task(_mark_offline(db, org_id, cluster_id))
                _cleanups.add(cleanup)
                cleanup.add_done_callback(_cleanups.discard)
                await asyncio.shield(cleanup)


# --- collections -------------------------------------------------------------------


class AuditNotStartable(Exception):
    """The collection cannot start right now; the message is safe to show to the user."""


async def start_collection(settings: Settings, session: AgentSession, run_id: UUID) -> None:
    """Asks the agent to collect and prepares to receive the chunks."""
    if session.collection is not None:
        raise AuditNotStartable("a collection is already running for this cluster")
    if len(settings.masking_key) < 16:
        raise AuditNotStartable("masking is not configured (MASKING_KEY)")
    masker = Masker(derive_tenant_key(settings.masking_key.encode(), session.org_id))
    session.collection = Collection(
        run_id, masker, max_compressed_bytes=settings.agent_max_compressed_bytes
    )
    try:
        await session.send(
            CollectRequestPayload(run_id=run_id, resources=[], exclude_namespaces=[])
        )
    except Exception as exc:
        session.collection.discard()
        session.collection = None
        raise AuditNotStartable("the agent connection was lost") from exc


async def _fail_run(db: Database, session: AgentSession, reason: str) -> None:
    collection, session.collection = session.collection, None
    if collection is None:
        return
    collection.discard()
    async with db.tenant(session.org_id) as tc:
        await audit_runs.mark_failed(tc, collection.run_id, error=reason)
    log.warning(
        "collection_failed",
        run_id=str(collection.run_id),
        cluster_id=str(session.cluster_id),
        reason=reason,
    )


async def _on_collection_message(
    store: ObjectStore,
    db: Database,
    session: AgentSession,
    payload: CollectChunkPayload | CollectDonePayload | ErrorPayload,
) -> None:
    collection = session.collection
    if collection is None or (payload.run_id is not None and payload.run_id != collection.run_id):
        # Nothing was requested (or not this run): ignore, an agent cannot start a run by itself.
        log.warning("unsolicited_collection_message", cluster_id=str(session.cluster_id))
        return
    if isinstance(payload, ErrorPayload):
        await _fail_run(db, session, f"agent reported {payload.code.value}: {payload.message}")
        return
    try:
        if isinstance(payload, CollectChunkPayload):
            # Decode, mask and spool off the event loop so heartbeats keep flowing.
            await asyncio.to_thread(collection.add_chunk, payload)
            return
        stats = await asyncio.to_thread(collection.finish, payload)
    except CollectionError as exc:
        await _fail_run(db, session, f"invalid collection data: {exc}")
        return

    try:
        key = await store.put_snapshot(session.org_id, collection.run_id, collection.path)
    except Exception as exc:
        log.error(
            "snapshot_upload_failed", run_id=str(collection.run_id), error_type=type(exc).__name__
        )
        await _fail_run(db, session, "could not store the snapshot")
        return
    session.collection = None
    collection.discard()
    async with db.tenant(session.org_id) as tc:
        await audit_runs.mark_collected(tc, collection.run_id, snapshot_key=key, stats=stats)
    for detector, count in stats["redactions"].items():
        session.metrics.masking_redactions.labels(detector).inc(count)
    log.info(
        "collection_stored",
        run_id=str(collection.run_id),
        cluster_id=str(session.cluster_id),
        objects=stats["objects"],
        chunks=stats["chunks"],
        truncated=stats["truncated"],
    )
