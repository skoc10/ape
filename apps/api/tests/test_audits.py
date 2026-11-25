"""Collection end to end on the Control Plane side (task 2.6)."""

import base64
import gzip
import hashlib
import json
import time
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from ape.core.config import Settings
from ape.gateway import protocol
from ape.gateway.protocol_gen import (
    CollectChunkPayload,
    CollectDonePayload,
    CollectRequestPayload,
    ErrorCode,
    ErrorPayload,
    MessageType,
    PingPayload,
)
from ape.storage import ObjectStore

from .conftest import Tenant, TokenFactory, bearer
from .test_gateway import connect, handshake

PEM = "-----BEGIN PRIVATE KEY-----\nMIIEvfakefakefake\n-----END PRIVATE KEY-----"
SECRETS = ["hunter2-plain-password", "tok3n-in-annotation", "MIIEvfakefakefake"]

PODS = [
    {
        "apiVersion": "v1",
        "metadata": {
            "name": "web",
            "namespace": "prod",
            "annotations": {"auth-token": "tok3n-in-annotation"},
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "nginx:latest",
                    "env": [
                        {"name": "DB_PASSWORD", "value": "hunter2-plain-password"},
                        {"name": "AGENT_SIDE", "value": "<redacted:len=9>"},
                    ],
                }
            ]
        },
    },
    {
        "apiVersion": "v1",
        "metadata": {"name": "tls", "namespace": "prod", "annotations": {"cert": PEM}},
    },
]
NODES = [{"apiVersion": "v1", "metadata": {"name": "node-1"}}]


@pytest.fixture
def auth_a(make_token: TokenFactory, tenant_a: Tenant) -> dict[str, str]:
    return bearer(make_token(tenant_a.user_id, tenant_a.org_id))


@pytest.fixture
def agent(client: TestClient, auth_a: dict[str, str]) -> dict[str, Any]:
    cluster = client.post("/v1/clusters", json={"name": "audit"}, headers=auth_a).json()
    token = client.post(f"/v1/clusters/{cluster['id']}/tokens", headers=auth_a).json()
    return {"cluster_id": cluster["id"], "token": token["token"]}


def chunk(
    run_id: str, seq: int, kind: str, objects: list[dict[str, Any]]
) -> tuple[CollectChunkPayload, bytes]:
    compressed = gzip.compress(json.dumps(objects).encode())
    payload = CollectChunkPayload(
        run_id=run_id,  # type: ignore[arg-type]
        seq=seq,
        kind=kind,
        count=len(objects),
        data=base64.b64encode(compressed).decode(),
    )
    return payload, compressed


def start(
    client: TestClient, ws: WebSocketTestSession, agent: dict[str, Any], headers: dict[str, str]
) -> str:
    resp = client.post(f"/v1/clusters/{agent['cluster_id']}/audits", headers=headers)
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["status"] == "collecting"
    envelope, request = protocol.decode(ws.receive_text())
    assert envelope.type is MessageType.collect_request
    assert isinstance(request, CollectRequestPayload)
    assert str(request.run_id) == run["id"]
    run_id: str = run["id"]
    return run_id


def wait_for_status(
    client: TestClient, run_id: str, headers: dict[str, str], wanted: str
) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = client.get(f"/v1/audits/{run_id}", headers=headers).json()
        if run["status"] == wanted:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run is {run.get('status')}, wanted {wanted}: {run.get('error')}")


def send_all(ws: WebSocketTestSession, run_id: str, **done_overrides: Any) -> None:
    digest = hashlib.sha256()
    first, raw = chunk(run_id, 0, "Pod", PODS)
    digest.update(raw)
    second, raw = chunk(run_id, 1, "Node", NODES)
    digest.update(raw)
    ws.send_text(protocol.encode(first))
    ws.send_text(protocol.encode(second))
    done: dict[str, Any] = {
        "run_id": run_id,
        "total_chunks": 2,
        "counts": {"Pod": 2, "Node": 1, "Ingress": 0},
        "sha256": digest.hexdigest(),
        "truncated": False,
    }
    done.update(done_overrides)
    ws.send_text(protocol.encode(CollectDonePayload.model_validate(done)))


async def test_collection_is_masked_and_stored_under_the_tenant_prefix(
    client: TestClient,
    settings: Settings,
    agent: dict[str, Any],
    auth_a: dict[str, str],
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        run_id = start(client, ws, agent, auth_a)
        send_all(ws, run_id)
        run = wait_for_status(client, run_id, auth_a, "collected")

        # The session is still usable afterwards.
        ws.send_text(protocol.encode(PingPayload()))
        assert protocol.decode(ws.receive_text())[0].type is MessageType.pong

    assert run["has_snapshot"] is True
    assert run["stats"]["counts"] == {"Node": 1, "Pod": 2}
    assert run["stats"]["objects"] == 3
    assert run["stats"]["truncated"] is False
    assert run["stats"]["redactions"] == {"key_name": 2, "known_format": 1}

    row = admin_db.execute(
        "select snapshot_key from audit_runs where id = %s", (run_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == f"{tenant_a.org_id}/{run_id}/snapshot.json.gz"

    import uuid

    stored = await ObjectStore(settings).get_snapshot(tenant_a.org_id, uuid.UUID(run_id))
    text = gzip.decompress(stored).decode()
    for secret in SECRETS:
        assert secret not in text
    assert "BEGIN PRIVATE KEY" not in text
    snapshot = json.loads(text)
    assert snapshot["run_id"] == run_id
    assert [o["kind"] for o in snapshot["objects"]] == ["Pod", "Pod", "Node"]
    env = snapshot["objects"][0]["spec"]["containers"][0]["env"]
    assert env[0]["value"].startswith("<secret:key_name:")
    assert env[1]["value"] == "<redacted:len=9>"
    assert snapshot["objects"][0]["spec"]["containers"][0]["image"] == "nginx:latest"

    assert 'ape_masking_redactions_total{detector="key_name"} 2.0' in client.get("/metrics").text
    assert (
        'ape_masking_redactions_total{detector="known_format"} 1.0' in client.get("/metrics").text
    )
    listed = client.get(f"/v1/clusters/{agent['cluster_id']}/audits", headers=auth_a).json()
    assert [r["id"] for r in listed] == [run_id]


def test_audit_needs_a_connected_agent(
    client: TestClient, agent: dict[str, Any], auth_a: dict[str, str]
) -> None:
    resp = client.post(f"/v1/clusters/{agent['cluster_id']}/audits", headers=auth_a)
    assert resp.status_code == 409
    assert client.get(f"/v1/clusters/{agent['cluster_id']}/audits", headers=auth_a).json() == []


def test_only_one_collection_at_a_time(
    client: TestClient, agent: dict[str, Any], auth_a: dict[str, str]
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        start(client, ws, agent, auth_a)
        again = client.post(f"/v1/clusters/{agent['cluster_id']}/audits", headers=auth_a)
        assert again.status_code == 409


def test_other_tenants_cannot_start_or_read_audits(
    client: TestClient,
    agent: dict[str, Any],
    auth_a: dict[str, str],
    make_token: TokenFactory,
    tenant_b: Tenant,
) -> None:
    auth_b = bearer(make_token(tenant_b.user_id, tenant_b.org_id))
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        assert (
            client.post(f"/v1/clusters/{agent['cluster_id']}/audits", headers=auth_b).status_code
            == 404
        )
        run_id = start(client, ws, agent, auth_a)
        assert client.get(f"/v1/audits/{run_id}", headers=auth_b).status_code == 404
        assert (
            client.get(f"/v1/clusters/{agent['cluster_id']}/audits", headers=auth_b).status_code
            == 404
        )


def test_checksum_mismatch_fails_the_run(
    client: TestClient, agent: dict[str, Any], auth_a: dict[str, str]
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        run_id = start(client, ws, agent, auth_a)
        send_all(ws, run_id, sha256="0" * 64)
        run = wait_for_status(client, run_id, auth_a, "failed")
    assert "checksum" in run["error"]
    assert run["has_snapshot"] is False


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [({"total_chunks": 3}, "chunks"), ({"counts": {"Pod": 5, "Node": 1}}, "counts")],
)
def test_inconsistent_collect_done_fails_the_run(
    client: TestClient,
    agent: dict[str, Any],
    auth_a: dict[str, str],
    overrides: dict[str, Any],
    expected: str,
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        run_id = start(client, ws, agent, auth_a)
        send_all(ws, run_id, **overrides)
        assert expected in wait_for_status(client, run_id, auth_a, "failed")["error"]


def bad_chunks(run_id: str) -> dict[str, CollectChunkPayload]:
    good, _ = chunk(run_id, 0, "Pod", PODS)
    bomb = base64.b64encode(gzip.compress(b"[" + b" " * (70 * 1024 * 1024) + b"]")).decode()
    return {
        "out of order": good.model_copy(update={"seq": 3}),
        "unknown kind": good.model_copy(update={"kind": "Secret"}),
        "wrong count": good.model_copy(update={"count": 9}),
        "not base64": good.model_copy(update={"data": "!!!"}),
        "not gzip": good.model_copy(update={"data": base64.b64encode(b"plain").decode()}),
        "not an array": good.model_copy(
            update={"data": base64.b64encode(gzip.compress(b'{"a":1}')).decode(), "count": 1}
        ),
        "decompression bomb": good.model_copy(update={"data": bomb, "count": 0}),
    }


@pytest.mark.parametrize(
    "case",
    ["out of order", "unknown kind", "wrong count", "not base64", "not gzip", "not an array"],
)
def test_invalid_chunks_fail_the_run_without_storing_anything(
    client: TestClient, agent: dict[str, Any], auth_a: dict[str, str], case: str
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        run_id = start(client, ws, agent, auth_a)
        ws.send_text(protocol.encode(bad_chunks(run_id)[case]))
        run = wait_for_status(client, run_id, auth_a, "failed")
        assert run["has_snapshot"] is False
        assert "invalid collection data" in run["error"]
        # A failed run frees the cluster for the next attempt.
        start(client, ws, agent, auth_a)


def test_decompression_bomb_is_rejected() -> None:
    import uuid

    from ape.gateway.collection import Collection, CollectionError
    from ape.sanitize import Masker

    run_id = str(uuid.uuid4())
    collection = Collection(
        uuid.UUID(run_id), Masker(b"k" * 32), max_compressed_bytes=50 * 1024 * 1024
    )
    try:
        with pytest.raises(CollectionError, match="inflates"):
            collection.add_chunk(bad_chunks(run_id)["decompression bomb"])
    finally:
        collection.discard()
    assert not collection.path.exists()


def test_agent_error_fails_the_run(
    client: TestClient, agent: dict[str, Any], auth_a: dict[str, str]
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        run_id = start(client, ws, agent, auth_a)
        error = ErrorPayload(
            run_id=run_id, code=ErrorCode.forbidden, message="list Deployment: forbidden"
        )  # type: ignore[arg-type]
        ws.send_text(protocol.encode(error))
        run = wait_for_status(client, run_id, auth_a, "failed")
    assert run["error"] == "agent reported forbidden: list Deployment: forbidden"


def test_disconnect_during_collection_fails_the_run(
    client: TestClient, agent: dict[str, Any], auth_a: dict[str, str]
) -> None:
    with connect(client, agent["token"]) as ws:
        handshake(ws)
        run_id = start(client, ws, agent, auth_a)
        first, _ = chunk(run_id, 0, "Pod", PODS)
        ws.send_text(protocol.encode(first))
    run = wait_for_status(client, run_id, auth_a, "failed")
    assert "disconnected" in run["error"]


def test_unsolicited_chunks_are_ignored(
    client: TestClient,
    agent: dict[str, Any],
    auth_a: dict[str, str],
    admin_db: psycopg.Connection[Any],
) -> None:
    import uuid

    with connect(client, agent["token"]) as ws:
        handshake(ws)
        stray, _ = chunk(str(uuid.uuid4()), 0, "Pod", PODS)
        ws.send_text(protocol.encode(stray))
        ws.send_text(protocol.encode(PingPayload()))
        assert protocol.decode(ws.receive_text())[0].type is MessageType.pong
    count = admin_db.execute(
        "select count(*) from audit_runs where cluster_id = %s", (agent["cluster_id"],)
    ).fetchone()
    assert count == (0,)


def test_snapshot_keys_are_built_only_from_the_tenant_context() -> None:
    import uuid

    from ape.storage import snapshot_key

    run_id = uuid.uuid4()
    assert snapshot_key("org_abc-123", run_id) == f"org_abc-123/{run_id}/snapshot.json.gz"
    for hostile in ["", "../other-org", "a/b", "org id", "org\\x", "."]:
        with pytest.raises(ValueError):
            snapshot_key(hostile, run_id)
