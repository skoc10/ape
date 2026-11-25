"""Queue worker entrypoint: rules, LLM, PDF (image: ape-worker).

A separate process and container from the API on purpose: if a report or an LLM
call exhausts memory, only this pod restarts. Agent WebSocket sessions live in
the API and are not affected, and the interrupted run goes back on the queue
once its lock is older than WORKER_STALE_AFTER_SECONDS.
"""

import asyncio
import os
import signal

import structlog
from prometheus_client import start_http_server

from ape.analysis.analyst import Analyst
from ape.analysis.model import build_model
from ape.analysis.pipeline import Pipeline
from ape.core.config import Settings, get_settings
from ape.core.db import Database
from ape.observability.logging import configure_logging
from ape.observability.metrics import Metrics
from ape.reports.builder import Reporter
from ape.storage import ObjectStore

log = structlog.get_logger("ape.worker")


async def run_worker(pipeline: Pipeline, stop: asyncio.Event) -> None:
    """Takes one run at a time until asked to stop."""
    poll = pipeline.settings.worker_poll_seconds
    while not stop.is_set():
        try:
            run = await pipeline.claim()
        except Exception as exc:
            log.error("queue_poll_failed", error_type=type(exc).__name__)
            run = None
        if run is not None:
            await pipeline.process(run)
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll)
        except TimeoutError:
            continue


async def serve(settings: Settings) -> None:
    metrics = Metrics()
    db = Database(settings.database_url, min_size=1, max_size=2)
    await db.open()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    start_http_server(settings.worker_metrics_port, registry=metrics.registry)
    log.info("worker_started", metrics_port=settings.worker_metrics_port)
    try:
        analyst = Analyst(settings, db, metrics, build_model(settings))
        log.info(
            "llm_configured", provider=settings.llm_provider or "none", model=settings.llm_model
        )
        store = ObjectStore(settings)
        reporter = Reporter(db, store, metrics)
        pipeline = Pipeline(
            settings, db, store, metrics, enrich=analyst.enrich_run, report=reporter.build
        )
        await run_worker(pipeline, stop)
    finally:
        await db.close()
        log.info("worker_stopped")


def main() -> None:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    settings = get_settings()
    configure_logging("ape-worker", settings.log_level)
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
