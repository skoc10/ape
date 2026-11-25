"""Findings endpoint (task 3.6)."""

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ape.analysis.analyst import Analyst
from ape.analysis.pipeline import Pipeline
from ape.core.config import Settings
from ape.core.db import Database
from ape.observability.metrics import Metrics
from ape.storage import ObjectStore

from .conftest import Tenant, TokenFactory, bearer
from .test_worker import queue_run, vulnerable_objects


@pytest.fixture(autouse=True)
def quiet_queue(admin_db: psycopg.Connection[Any]) -> None:
    admin_db.execute(
        "update audit_runs set status = 'failed', error = 'parked by tests' "
        "where status in ('collected', 'analyzing', 'reporting')"
    )


async def analysed_run(
    settings: Settings,
    tenant: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
    cluster_id: uuid.UUID | None = None,
) -> uuid.UUID:
    db = Database(settings.database_url, min_size=1, max_size=2)
    await db.open()
    try:
        metrics = Metrics()
        analyst = Analyst(settings, db, metrics, None)
        pipeline = Pipeline(settings, db, ObjectStore(settings), metrics, enrich=analyst.enrich_run)
        run_id = await queue_run(admin_db, settings, tenant, tmp_path, objects=vulnerable_objects())
        if cluster_id is not None:
            admin_db.execute(
                "update audit_runs set cluster_id = %s where id = %s", (cluster_id, run_id)
            )
        run = await pipeline.claim()
        assert run is not None and await pipeline.process(run) == "completed"
        return run_id
    finally:
        await db.close()


async def test_findings_are_listed_filtered_and_tenant_scoped(
    client: TestClient,
    settings: Settings,
    make_token: TokenFactory,
    tenant_a: Tenant,
    tenant_b: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    run_id = await analysed_run(settings, tenant_a, admin_db, tmp_path)
    auth_a = bearer(make_token(tenant_a.user_id, tenant_a.org_id))
    auth_b = bearer(make_token(tenant_b.user_id, tenant_b.org_id))

    audit = client.get(f"/v1/audits/{run_id}", headers=auth_a).json()
    assert audit["status"] == "completed"
    assert audit["stats"]["findings"]["total"] == 18 and audit["stats"]["summary"]

    listed = client.get(f"/v1/audits/{run_id}/findings", headers=auth_a).json()
    assert len(listed) == 18
    severities = [f["severity"] for f in listed]
    assert severities == sorted(severities, key=["critical", "high", "medium", "low", "info"].index)
    first = listed[0]
    assert first["explanation"] and first["remediation"] and first["recurring"] is False
    assert {"rule_id", "resource_kind", "resource_ns", "resource_name", "evidence", "patch"} <= set(
        first
    )

    critical = client.get(f"/v1/audits/{run_id}/findings?severity=critical", headers=auth_a).json()
    assert {f["rule_id"] for f in critical} == {"SEC-001", "RBAC-001", "RBAC-002"}
    rbac = client.get(f"/v1/audits/{run_id}/findings?category=rbac", headers=auth_a).json()
    assert len(rbac) == 5 and {f["category"] for f in rbac} == {"rbac"}
    both = client.get(
        f"/v1/audits/{run_id}/findings?category=rbac&severity=critical", headers=auth_a
    ).json()
    assert len(both) == 2
    assert (
        client.get(
            f"/v1/audits/{run_id}/findings?severity=catastrophic", headers=auth_a
        ).status_code
        == 422
    )

    assert client.get(f"/v1/audits/{run_id}/findings", headers=auth_b).status_code == 404
    assert client.get(f"/v1/audits/{run_id}/findings").status_code == 401
    assert client.get(f"/v1/audits/{uuid.uuid4()}/findings", headers=auth_a).status_code == 404


async def test_second_audit_of_the_same_cluster_marks_findings_as_recurring(
    client: TestClient,
    settings: Settings,
    make_token: TokenFactory,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    first = await analysed_run(settings, tenant_a, admin_db, tmp_path)
    row = admin_db.execute("select cluster_id from audit_runs where id = %s", (first,)).fetchone()
    assert row is not None
    admin_db.execute(
        "update audit_runs set created_at = now() - interval '1 day' where id = %s", (first,)
    )
    second = await analysed_run(settings, tenant_a, admin_db, tmp_path, cluster_id=row[0])
    auth = bearer(make_token(tenant_a.user_id, tenant_a.org_id))

    assert all(
        f["recurring"] for f in client.get(f"/v1/audits/{second}/findings", headers=auth).json()
    )
    assert not any(
        f["recurring"] for f in client.get(f"/v1/audits/{first}/findings", headers=auth).json()
    )
