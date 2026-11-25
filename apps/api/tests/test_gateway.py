"""Agent WebSocket gateway (task 2.2)."""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from ape.core.config import Settings
from ape.gateway import protocol
from ape.gateway.protocol_gen import (
    HelloAckPayload,
    HelloPayload,
    MessageType,
    PingPayload,
    PongPayload,
)
from ape.server import create_app

from .conftest import SigningKey, Tenant, TokenFactory, bearer

HELLO = HelloPayload(
    agent_version="0.1.0", k8s_version="v1.31.2", node_count=3, capabilities=["metrics"]
)
TS = "2026-11-25T05:00:00Z"


@pytest.fixture
def auth_a(make_token: TokenFactory, tenant_a: Tenant) -> dict[str, str]:
    return bearer(make_token(tenant_a.user_id, tenant_a.org_id))


@pytest.fixture
def agent(client: TestClient, auth_a: dict[str, str]) -> dict[str, Any]:
    """A cluster of tenant A with one live agent token."""
    cluster = client.post("/v1/clusters", json={"name": "gw"}, headers=auth_a).json()
    token = client.post(f"/v1/clusters/{cluster['id']}/tokens", headers=auth_a).json()
    return {"cluster_id": cluster["id"], "token": token["token"], "token_id": token["id"]}


@contextmanager
def connect(client: TestClient, token: str | None) -> Iterator[WebSocketTestSession]:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    with client.websocket_connect("/v1/agent", headers=headers) as ws:
        yield ws


def handshake(ws: WebSocketTestSession) -> HelloAckPayload:
    ws.send_text(protocol.encode(HELLO))
    envelope, payload = protocol.decode(ws.receive_text())
    assert envelope.type is MessageType.hello_ack
    assert isinstance(payload, HelloAckPayload)
    return payload


def close_code(ws: WebSocketTestSession) -> int:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        ws.receive_text()
    return excinfo.value.code


def cluster_row(admin_db: psycopg.Connection[Any], cluster_id: str) -> tuple[Any, ...]:
    row = admin_db.execute(
        "select status, k8s_version, agent_version, last_seen_at from clusters where id = %s",
        (cluster_id,),
    ).fetchone()
    assert row is not None
    return row


def metric(client: TestClient, line_prefix: str) -> float:
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(line_prefix):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def revoke(
    client: TestClient, agent: dict[str, Any], headers: dict[str, str], token_id: str
) -> int:
    url = f"/v1/clusters/{agent['cluster_id']}/tokens/{token_id}"
    return client.delete(url, headers=headers).status_code


# --- authentication ----------------------------------------------------------------


@pytest.mark.parametrize("token", [None, "ape_" + "0" * 43, "not-a-token", "ape_" + "x" * 500])
def test_missing_or_invalid_token_is_closed_with_4401(
    client: TestClient, token: str | None
) -> None:
    with connect(client, token) as ws:
        assert close_code(ws) == 4401


def test_revoked_token_cannot_connect(
    client: TestClient, auth_a: dict[str, str], agent: dict[str, Any]
) -> None:
    assert revoke(client, agent, auth_a, agent["token_id"]) == 200
    with connect(client, agent["token"]) as ws:
        assert close_code(ws) == 4401


def test_a_user_jwt_is_not_an_agent_token(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    with connect(client, make_token(tenant_a.user_id, tenant_a.org_id)) as ws:
        assert close_code(ws) == 4401


# --- hello / hello_ack -------------------------------------------------------------


def test_hello_marks_the_cluster_connected(
    client: TestClient,
    agent: dict[str, Any],
    auth_a: dict[str, str],
    admin_db: psycopg.Connection[Any],
) -> None:
    with connect(client, agent["token"]) as ws:
        ack = handshake(ws)
        assert ack.heartbeat_interval_seconds == 30
        assert ack.limits.max_pods == 5000
        assert ack.limits.max_frame_bytes == 512 * 1024
        assert len(ack.session_id) == 26

        status, k8s_version, agent_version, last_seen = cluster_row(admin_db, agent["cluster_id"])
        assert (status, k8s_version, agent_version) == ("connected", "v1.31.2", "0.1.0")
        assert last_seen is not None
        listed = client.get("/v1/clusters", headers=auth_a).json()["items"][0]
        assert listed["status"] == "connected"
        assert metric(client, "ape_agent_sessions ") == 1.0

    deadline = time.monotonic() + 2
    while (
        cluster_row(admin_db, agent["cluster_id"])[0] != "offline" and time.monotonic() < deadline
    ):
        time.sleep(0.02)
    assert cluster_row(admin_db, agent["cluster_id"])[0] == "offline"
    assert metric(client, "ape_agent_sessions ") == 0.0


def test_ping_gets_pong_and_refreshes_last_seen(
    client: TestClient, agent: dict[str, Any], admin_db: psycopg.Connection[Any]
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        before = cluster_row(admin_db, agent["cluster_id"])[3]
        ws.send_text(protocol.encode(PingPayload()))
        envelope, payload = protocol.decode(ws.receive_text())
        assert envelope.type is MessageType.pong
        assert isinstance(payload, PongPayload)
        assert cluster_row(admin_db, agent["cluster_id"])[3] > before

    assert metric(client, 'ape_agent_messages_total{direction="in",type="hello"}') == 1.0
    assert metric(client, 'ape_agent_messages_total{direction="in",type="ping"}') == 1.0
    assert metric(client, 'ape_agent_messages_total{direction="out",type="hello_ack"}') == 1.0
    assert metric(client, 'ape_agent_messages_total{direction="out",type="pong"}') == 1.0


def test_first_message_must_be_hello(client: TestClient, agent: dict[str, Any]) -> None:
    with connect(client, agent["token"]) as ws:
        ws.send_text(protocol.encode(PingPayload()))
        assert close_code(ws) == 4400


def test_missing_hello_times_out(
    settings: Settings, signing_key: SigningKey, client: TestClient, agent: dict[str, Any]
) -> None:
    async def fetch_jwks() -> dict[str, Any]:
        return {"keys": [signing_key.jwk]}

    impatient = settings.model_copy(update={"agent_hello_timeout_seconds": 0.2})
    app = create_app(impatient, jwks_fetcher=fetch_jwks)
    with TestClient(app) as other, connect(other, agent["token"]) as ws:
        assert close_code(ws) == 4408


# --- protocol hardening ------------------------------------------------------------


def _frame(**overrides: Any) -> str:
    base: dict[str, Any] = {"v": 1, "type": "ping", "id": "0" * 26, "ts": TS, "payload": {}}
    return json.dumps({**base, **overrides})


@pytest.mark.parametrize(
    "frame",
    [
        "not json",
        _frame(type="exec"),
        _frame(payload={"org_id": "x"}),
        _frame(org_id="other"),
        "x" * (512 * 1024 + 1),
    ],
    ids=["not-json", "unknown-type", "payload-org-id", "envelope-org-id", "oversized"],
)
def test_invalid_frames_close_the_session_with_4400(
    client: TestClient, agent: dict[str, Any], frame: str
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        ws.send_text(frame)
        assert close_code(ws) == 4400


def test_session_is_bound_to_the_tokens_tenant(
    client: TestClient,
    agent: dict[str, Any],
    make_token: TokenFactory,
    tenant_b: Tenant,
    admin_db: psycopg.Connection[Any],
) -> None:
    auth_b = bearer(make_token(tenant_b.user_id, tenant_b.org_id))
    cluster_b = client.post("/v1/clusters", json={"name": "gw"}, headers=auth_b).json()
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        # A's agent is connected; B's cluster of the same name is untouched.
        assert cluster_row(admin_db, cluster_b["id"])[0] == "pending"
        assert client.get("/v1/clusters", headers=auth_b).json()["items"][0]["status"] == "pending"


# --- lifecycle ---------------------------------------------------------------------


def test_revoking_the_token_closes_the_open_socket_with_4401(
    client: TestClient, agent: dict[str, Any], auth_a: dict[str, str]
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        assert revoke(client, agent, auth_a, agent["token_id"]) == 200
        assert close_code(ws) == 4401


def test_revoking_another_token_keeps_the_session(
    client: TestClient, agent: dict[str, Any], auth_a: dict[str, str]
) -> None:
    other = client.post(f"/v1/clusters/{agent['cluster_id']}/tokens", headers=auth_a).json()
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        assert revoke(client, agent, auth_a, other["id"]) == 200
        ws.send_text(protocol.encode(PingPayload()))
        assert protocol.decode(ws.receive_text())[0].type is MessageType.pong


def test_expired_token_is_dropped_at_the_next_heartbeat(
    client: TestClient, agent: dict[str, Any], admin_db: psycopg.Connection[Any]
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        admin_db.execute(
            "update agent_tokens set expires_at = now() - interval '1 second' where id = %s",
            (agent["token_id"],),
        )
        ws.send_text(protocol.encode(PingPayload()))
        assert close_code(ws) == 4401


def test_a_second_connection_replaces_the_first(
    client: TestClient, agent: dict[str, Any], admin_db: psycopg.Connection[Any]
) -> None:
    with connect(client, agent["token"]) as first:
        handshake(first)
        with connect(client, agent["token"]) as second:
            handshake(second)
            assert close_code(first) == 4409
            # The replaced session must not mark the cluster offline on its way out.
            second.send_text(protocol.encode(PingPayload()))
            assert protocol.decode(second.receive_text())[0].type is MessageType.pong
            assert cluster_row(admin_db, agent["cluster_id"])[0] == "connected"


def test_stale_connected_status_reads_as_offline(
    client: TestClient,
    agent: dict[str, Any],
    auth_a: dict[str, str],
    admin_db: psycopg.Connection[Any],
) -> None:
    # As if the API process died without a clean disconnect.
    admin_db.execute(
        "update clusters set status = 'connected', last_seen_at = now() - interval '5 minutes' "
        "where id = %s",
        (agent["cluster_id"],),
    )
    assert client.get("/v1/clusters", headers=auth_a).json()["items"][0]["status"] == "offline"
