"""What the worker does with one claimed run: rules, (LLM), report.

Every database statement here runs inside ``db.tenant(org_id)`` with the org_id
that came out of the queue, so the worker is under the same RLS policies as the API.
"""

import asyncio
import contextlib
import gzip
import json
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from ape.core.config import Settings
from ape.core.db import Database
from ape.observability.metrics import Metrics
from ape.repos import audit_runs, findings
from ape.rules import Finding, run_rules
from ape.storage import ObjectStore

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    id: UUID
    org_id: str
    cluster_id: UUID


# Optional enrichment stage (LLM, task 3.5). Receives the run, the masked objects and the
# findings; returns extra stats. It must never raise: the report is produced without it.
Enricher = Callable[[ClaimedRun, list[dict[str, Any]], list[Finding]], Awaitable[dict[str, Any]]]


# Report stage (score, HTML, PDF; week 4). Receives the run and the stats gathered so far.
ReportBuilder = Callable[[ClaimedRun, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class Pipeline:
    settings: Settings
    db: Database
    store: ObjectStore
    metrics: Metrics
    enrich: Enricher | None = None
    report: ReportBuilder | None = None

    async def claim(self) -> ClaimedRun | None:
        async with self.db.system() as sc:
            row = await audit_runs.claim(
                sc,
                stale_after_seconds=self.settings.worker_stale_after_seconds,
                max_attempts=self.settings.worker_max_attempts,
            )
        return None if row is None else ClaimedRun(row["id"], row["org_id"], row["cluster_id"])

    async def process(self, run: ClaimedRun) -> str:
        """Runs all stages for one run and returns its final status. Never raises."""
        bound = log.bind(run_id=str(run.id), cluster_id=str(run.cluster_id))
        heartbeat = asyncio.create_task(self._heartbeat(run))
        try:
            stats = await self._stages(run)
        except Exception as exc:
            # The exception text may quote data; only its type is stored and logged.
            bound.error("audit_failed", error_type=type(exc).__name__)
            async with self.db.tenant(run.org_id) as tc:
                await audit_runs.fail_processing(
                    tc, run.id, error=f"analysis failed ({type(exc).__name__})"
                )
            self.metrics.audit_runs.labels("failed").inc()
            return "failed"
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        async with self.db.tenant(run.org_id) as tc:
            completed = await audit_runs.complete(tc, run.id, stats=stats)
        status = "completed" if completed else "superseded"
        self.metrics.audit_runs.labels(status).inc()
        bound.info("audit_finished", status=status, findings=stats["findings"]["total"])
        return status

    async def _stages(self, run: ClaimedRun) -> dict[str, Any]:
        durations: dict[str, float] = {}

        async with self._stage("rules", durations):
            raw = await self.store.get_snapshot(run.org_id, run.id)
            objects = await asyncio.to_thread(_load_objects, raw)
            found = await asyncio.to_thread(run_rules, objects)
            async with self.db.tenant(run.org_id) as tc:
                await findings.insert_many(tc, run.id, found)

        extra: dict[str, Any] = {}
        if self.enrich is not None and found:
            async with self._stage("llm", durations):
                extra = await self.enrich(run, objects, found)

        report: dict[str, Any] = {}
        async with self._stage("report", durations):
            async with self.db.tenant(run.org_id) as tc:
                await audit_runs.set_stage(tc, run.id, "reporting")
            if self.report is not None:
                report = await self.report(run, extra)

        severities = Counter(f.severity for f in found)
        return {
            **extra,
            **report,
            "findings": {"total": len(found), **dict(sorted(severities.items()))},
            "durations": {stage: round(seconds, 3) for stage, seconds in durations.items()},
        }

    @contextlib.asynccontextmanager
    async def _stage(self, name: str, durations: dict[str, float]) -> AsyncIterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            durations[name] = time.perf_counter() - start
            self.metrics.audit_stage_seconds.labels(name).observe(durations[name])

    async def _heartbeat(self, run: ClaimedRun) -> None:
        """Keeps locked_at fresh so a long but healthy job is not handed to another worker."""
        while True:
            await asyncio.sleep(self.settings.worker_heartbeat_seconds)
            try:
                async with self.db.tenant(run.org_id) as tc:
                    await audit_runs.heartbeat(tc, run.id)
            except Exception as exc:
                # A missed beat must not take the run (or the worker) down with it: the
                # next one may succeed, and a lock that really goes stale is reclaimed.
                log.warning("heartbeat_failed", run_id=str(run.id), error_type=type(exc).__name__)


def _load_objects(raw: bytes) -> list[dict[str, Any]]:
    document = json.loads(gzip.decompress(raw))
    objects = document.get("objects") if isinstance(document, dict) else None
    if not isinstance(objects, list):
        raise ValueError("snapshot has no object list")
    return [o for o in objects if isinstance(o, dict)]
