"""Worker queue and pipeline (task 3.4)."""

import asyncio
import gzip
import json
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from ape.analysis.pipeline import ClaimedRun, Pipeline
from ape.core.config import Settings
from ape.core.db import Database
from ape.observability.metrics import Metrics
from ape.rules import Finding
from ape.sanitize import Masker
from ape.storage import ObjectStore
from ape.worker import run_worker

from .conftest import Tenant

VULNERABLE = Path(__file__).resolve().parents[3] / "testdata" / "vulnerable"


def vulnerable_objects() -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for path in sorted((VULNERABLE / "manifests").glob("*.yaml")):
        objects.extend(doc for doc in yaml.safe_load_all(path.read_text()) if doc)
    masked: list[dict[str, Any]] = Masker(b"k" * 32).mask(objects)
    return masked


@pytest.fixture(autouse=True)
def quiet_queue(admin_db: psycopg.Connection[Any]) -> None:
    """Park whatever a developer database has queued, so each test sees only its own runs."""
    admin_db.execute(
        "update audit_runs set status = 'failed', error = 'parked by tests' "
        "where status in ('collected', 'analyzing', 'reporting')"
    )


@pytest.fixture
async def pipeline(settings: Settings) -> Any:
    db = Database(settings.database_url, min_size=1, max_size=4)
    await db.open()
    fast = settings.model_copy(
        update={"worker_poll_seconds": 0.05, "worker_heartbeat_seconds": 0.05}
    )
    yield Pipeline(fast, db, ObjectStore(settings), Metrics())
    await db.close()


async def queue_run(
    admin_db: psycopg.Connection[Any],
    settings: Settings,
    tenant: Tenant,
    tmp_path: Path,
    *,
    objects: list[dict[str, Any]] | None = None,
    status: str = "collected",
    age_minutes: int = 0,
    locked_minutes_ago: int | None = None,
    attempts: int = 0,
    upload: bool = True,
) -> uuid.UUID:
    cluster_id, run_id = uuid.uuid4(), uuid.uuid4()
    admin_db.execute(
        "insert into clusters (id, org_id, name) values (%s, %s, %s)",
        (cluster_id, tenant.org_id, f"c-{cluster_id.hex[:8]}"),
    )
    admin_db.execute(
        "insert into audit_runs (id, org_id, cluster_id, triggered_by, status, attempts, created_at, locked_at) "
        "values (%s, %s, %s, %s, %s, %s, now() - make_interval(mins => %s), "
        "case when %s::int is null then null else now() - make_interval(mins => %s::int) end)",
        (
            run_id,
            tenant.org_id,
            cluster_id,
            tenant.user_id,
            status,
            attempts,
            age_minutes,
            locked_minutes_ago,
            locked_minutes_ago,
        ),
    )
    if upload:
        path = tmp_path / f"{run_id}.json.gz"
        document = {
            "version": 1,
            "run_id": str(run_id),
            "objects": objects if objects is not None else [],
        }
        path.write_bytes(gzip.compress(json.dumps(document).encode()))
        await ObjectStore(settings).put_snapshot(tenant.org_id, run_id, path)
    return run_id


def run_row(admin_db: psycopg.Connection[Any], run_id: uuid.UUID) -> dict[str, Any]:
    with admin_db.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "select status, error, stats, attempts, locked_at, finished_at from audit_runs where id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    assert row is not None
    return row


async def test_worker_turns_a_collected_snapshot_into_findings(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    run_id = await queue_run(admin_db, settings, tenant_a, tmp_path, objects=vulnerable_objects())

    run = await pipeline.claim()
    assert run is not None and run.id == run_id and run.org_id == tenant_a.org_id
    assert run_row(admin_db, run_id)["status"] == "analyzing"
    assert await pipeline.process(run) == "completed"

    row = run_row(admin_db, run_id)
    assert row["status"] == "completed"
    assert row["finished_at"] is not None and row["locked_at"] is None and row["attempts"] == 1
    assert row["stats"]["findings"]["total"] == 18
    assert row["stats"]["findings"]["critical"] == 3
    assert set(row["stats"]["durations"]) == {"rules", "report"}

    expected = yaml.safe_load((VULNERABLE / "expected.yaml").read_text())
    stored = admin_db.execute(
        "select rule_id, resource_kind || '/' || coalesce(resource_ns, '-') || '/' || resource_name, org_id "
        "from findings where audit_run_id = %s",
        (run_id,),
    ).fetchall()
    actual: dict[str, list[str]] = {}
    for rule_id, resource, org_id in stored:
        assert org_id == tenant_a.org_id
        actual.setdefault(rule_id, []).append(resource)
    assert {k: sorted(v) for k, v in actual.items()} == {k: sorted(v) for k, v in expected.items()}

    text = pipeline.metrics.registry.get_sample_value
    assert text("ape_audit_runs_total", {"status": "completed"}) == 1.0
    assert text("ape_audit_stage_seconds_count", {"stage": "rules"}) == 1.0


async def test_queue_is_fifo_and_concurrent_claims_never_collide(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    tenant_b: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    oldest = await queue_run(admin_db, settings, tenant_a, tmp_path, age_minutes=30, upload=False)
    middle = await queue_run(admin_db, settings, tenant_b, tmp_path, age_minutes=20, upload=False)
    newest = await queue_run(admin_db, settings, tenant_a, tmp_path, age_minutes=10, upload=False)

    claims = await asyncio.gather(*(pipeline.claim() for _ in range(6)))
    claimed = [c for c in claims if c is not None]
    assert sorted(c.id for c in claimed) == sorted([oldest, middle, newest])  # each exactly once
    assert {c.id: c.org_id for c in claimed}[middle] == tenant_b.org_id
    assert await pipeline.claim() is None


async def test_runs_in_other_states_are_not_claimed(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    for status in ("queued", "collecting", "completed", "failed"):
        await queue_run(admin_db, settings, tenant_a, tmp_path, status=status, upload=False)
    await queue_run(
        admin_db,
        settings,
        tenant_a,
        tmp_path,
        status="analyzing",
        locked_minutes_ago=1,
        upload=False,
    )
    assert await pipeline.claim() is None


async def test_a_run_whose_worker_died_goes_back_on_the_queue(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    # Claimed 11 minutes ago by a worker that was killed: the lock is older than 10 minutes.
    run_id = await queue_run(
        admin_db,
        settings,
        tenant_a,
        tmp_path,
        objects=vulnerable_objects(),
        status="analyzing",
        locked_minutes_ago=11,
        attempts=1,
    )
    run = await pipeline.claim()
    assert run is not None and run.id == run_id
    assert run_row(admin_db, run_id)["attempts"] == 2
    assert await pipeline.process(run) == "completed"


async def test_a_run_that_keeps_killing_its_worker_is_given_up(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    run_id = await queue_run(
        admin_db,
        settings,
        tenant_a,
        tmp_path,
        status="reporting",
        locked_minutes_ago=30,
        attempts=3,
        upload=False,
    )
    assert await pipeline.claim() is None
    row = run_row(admin_db, run_id)
    assert row["status"] == "failed"
    assert "3 attempts" in row["error"]


async def test_a_broken_snapshot_fails_the_run_without_leaking_details(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    run_id = await queue_run(admin_db, settings, tenant_a, tmp_path, upload=False)  # nothing in S3
    run = await pipeline.claim()
    assert run is not None
    assert await pipeline.process(run) == "failed"
    row = run_row(admin_db, run_id)
    assert row["status"] == "failed" and row["locked_at"] is None
    assert row["error"].startswith("analysis failed (")
    assert (
        pipeline.metrics.registry.get_sample_value("ape_audit_runs_total", {"status": "failed"})
        == 1.0
    )


async def test_heartbeat_keeps_a_long_job_locked(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    run_id = await queue_run(admin_db, settings, tenant_a, tmp_path, objects=vulnerable_objects())
    locks: list[Any] = []

    async def slow_enrich(
        run: ClaimedRun, objects: list[dict[str, Any]], found: list[Finding]
    ) -> dict[str, Any]:
        locks.append(run_row(admin_db, run.id)["locked_at"])
        await asyncio.sleep(0.3)
        locks.append(run_row(admin_db, run.id)["locked_at"])
        return {"llm": {"enabled": False}}

    pipeline.enrich = slow_enrich
    run = await pipeline.claim()
    assert run is not None
    assert await pipeline.process(run) == "completed"
    assert locks[1] > locks[0]  # refreshed while the stage was running
    assert run_row(admin_db, run_id)["stats"]["llm"] == {"enabled": False}
    assert "llm" in run_row(admin_db, run_id)["stats"]["durations"]


async def test_worker_loop_drains_the_queue_and_stops_on_request(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    tenant_b: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    first = await queue_run(
        admin_db, settings, tenant_a, tmp_path, objects=vulnerable_objects(), age_minutes=5
    )
    second = await queue_run(admin_db, settings, tenant_b, tmp_path, objects=[], age_minutes=1)
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(pipeline, stop))
    for _ in range(200):
        if all(run_row(admin_db, r)["status"] == "completed" for r in (first, second)):
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert run_row(admin_db, first)["stats"]["findings"]["total"] == 18
    assert run_row(admin_db, second)["stats"]["findings"] == {"total": 0}
    # Tenant B's run produced nothing and none of A's findings ended up under B.
    count = admin_db.execute(
        "select count(*) from findings where org_id = %s", (tenant_b.org_id,)
    ).fetchone()
    assert count == (0,)


async def test_a_failing_heartbeat_does_not_fail_the_run(
    pipeline: Pipeline,
    settings: Settings,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await queue_run(admin_db, settings, tenant_a, tmp_path, objects=vulnerable_objects())
    beats = 0

    async def broken_heartbeat(tc: Any, run_id: uuid.UUID) -> None:
        nonlocal beats
        beats += 1
        raise RuntimeError("connection reset")

    async def slow_enrich(
        run: ClaimedRun, objects: list[dict[str, Any]], found: list[Finding]
    ) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {}

    monkeypatch.setattr("ape.repos.audit_runs.heartbeat", broken_heartbeat)
    pipeline.enrich = slow_enrich
    run = await pipeline.claim()
    assert run is not None
    assert await pipeline.process(run) == "completed"
    assert beats >= 2  # it kept trying instead of dying on the first error
    assert run_row(admin_db, run_id)["status"] == "completed"
