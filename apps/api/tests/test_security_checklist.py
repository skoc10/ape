"""Security checklist (task 4.5): the guarantees that are not owned by a single module.

* logs never contain Kubernetes object content, on the API or in the worker;
* the full path from agent chunk to LLM prompt, stored findings, report and logs
  carries none of the planted secrets.

The other checklist items live next to their code: tenant isolation in
db/tests/tenant_isolation.sql, "no secret reaches the LLM" in test_analyst.py, and the
agent's read-only RBAC in deploy/helm/ape-agent/ci/rbac-check.sh (see docs/SECURITY.md).
"""

import io
import json
import logging
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

from ape.analysis.analyst import Analyst
from ape.analysis.pipeline import Pipeline
from ape.core.config import Settings
from ape.core.db import Database
from ape.gateway import protocol
from ape.gateway.protocol_gen import CollectDonePayload
from ape.observability.metrics import Metrics
from ape.reports.builder import Reporter
from ape.storage import ObjectStore

from .conftest import Tenant, TokenFactory, bearer
from .test_analyst import Scripted
from .test_audits import chunk, start, wait_for_status
from .test_gateway import connect, handshake

VULNERABLE = Path(__file__).resolve().parents[3] / "testdata" / "vulnerable"
PLANTED = (VULNERABLE / "planted-secrets.txt").read_text().split()

# Strings that only exist inside the collected objects. None of them may be logged.
OBJECT_CONTENT = [
    "storefront",
    "orders-db",
    "vuln-shop",
    "vuln-batch",
    "nightly-export",
    "registry.k8s.io/pause",
    "docker.sock",
    "secret-reader",
    "vuln-everything",
]


def raw_objects() -> dict[str, list[dict[str, Any]]]:
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((VULNERABLE / "manifests").glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if doc:
                by_kind.setdefault(doc["kind"], []).append(doc)
    return by_kind


@pytest.fixture
def captured_logs() -> Any:
    """Everything the process logs, at DEBUG, from every logger."""
    stream = io.StringIO()
    root = logging.getLogger()
    previous_level = root.level
    yield stream
    root.setLevel(previous_level)


async def test_no_object_content_and_no_secret_anywhere_downstream(
    client: TestClient,
    settings: Settings,
    make_token: TokenFactory,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    captured_logs: io.StringIO,
) -> None:
    admin_db.execute(
        "update audit_runs set status = 'failed', error = 'parked by tests' "
        "where status in ('collected', 'analyzing', 'reporting')"
    )
    auth = bearer(make_token(tenant_a.user_id, tenant_a.org_id))
    cluster = client.post("/v1/clusters", json={"name": "checklist"}, headers=auth).json()
    token = client.post(f"/v1/clusters/{cluster['id']}/tokens", headers=auth).json()["token"]
    agent = {"cluster_id": cluster["id"], "token": token}

    # Capture from here on: the app configured logging when the client fixture started it.
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    original = handler.setStream(captured_logs)
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        # --- API: the agent streams UNMASKED objects (as if its own sanitizer had failed) ---
        with connect(client, token) as ws:
            handshake(ws)
            run_id = start(client, ws, agent, auth)
            import hashlib

            digest, counts, seq = hashlib.sha256(), {}, 0
            for kind, objects in raw_objects().items():
                payload, compressed = chunk(run_id, seq, kind, objects)
                ws.send_text(protocol.encode(payload))
                digest.update(compressed)
                counts[kind] = len(objects)
                seq += 1
            done = {
                "run_id": run_id,
                "total_chunks": seq,
                "counts": counts,
                "sha256": digest.hexdigest(),
                "truncated": False,
            }
            ws.send_text(protocol.encode(CollectDonePayload.model_validate(done)))
            wait_for_status(client, run_id, auth, "collected")
            # A malformed frame makes the gateway log a protocol error: it must not echo the frame.
            ws.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "collect_chunk",
                        "payload": {"leak": PLANTED[0], "name": "storefront"},
                    }
                )
            )

        # --- worker: rules, LLM (scripted), report ---
        db = Database(settings.database_url, min_size=1, max_size=3)
        await db.open()
        try:
            metrics, store = Metrics(), ObjectStore(settings)
            llm = Scripted()
            configured = settings.model_copy(
                update={"llm_provider": "test", "llm_model": "scripted", "llm_output_mode": "tool"}
            )
            pipeline = Pipeline(
                configured,
                db,
                store,
                metrics,
                enrich=Analyst(configured, db, metrics, llm.model()).enrich_run,
                report=Reporter(db, store, metrics, pdf=False).build,
            )
            run = await pipeline.claim()
            assert run is not None and str(run.id) == run_id
            assert await pipeline.process(run) == "completed"
            snapshot = (await store.get_snapshot(tenant_a.org_id, run.id)).hex()
            report = (await store.get_report(tenant_a.org_id, run.id, "html") or b"").decode()
        finally:
            await db.close()
    finally:
        handler.setStream(original)

    logs = captured_logs.getvalue()
    # The run really went through every stage and logged about it...
    for event in (
        "audit_started",
        "collection_stored",
        "agent_protocol_error",
        "audit_finished",
        "report_built",
    ):
        assert event in logs, f"{event} missing from the logs"
    # ...using only identifiers, kinds, counts and durations.
    assert [text for text in OBJECT_CONTENT if text in logs] == []
    assert [secret for secret in PLANTED if secret in logs] == []
    assert token not in logs

    # No planted secret in what the LLM saw, in the stored findings, or in the report.
    import gzip

    stored_snapshot = gzip.decompress(bytes.fromhex(snapshot)).decode()
    findings = json.dumps(client.get(f"/v1/audits/{run_id}/findings", headers=auth).json())
    prompts = "\n".join(llm.prompts)
    assert llm.calls >= 1 and "storefront" in prompts  # the model did get the (masked) manifests
    for name, text in (
        ("snapshot", stored_snapshot),
        ("LLM prompt", prompts),
        ("findings", findings),
        ("report", report),
    ):
        leaked = [secret for secret in PLANTED if secret in text]
        assert leaked == [], f"{name} contains {leaked}"
    assert "storefront" in report and "<secret:" not in report.split("Proposed patch")[0][:0]


def test_error_responses_do_not_echo_request_content(
    client: TestClient, make_token: TokenFactory, tenant_a: Tenant
) -> None:
    auth = bearer(make_token(tenant_a.user_id, tenant_a.org_id))
    resp = client.post("/v1/clusters", json={"name": "Bad Name With hunter2-secret"}, headers=auth)
    assert resp.status_code == 422
    # FastAPI's default 422 body repeats the rejected input; ours must not.
    assert "hunter2-secret" not in resp.text
