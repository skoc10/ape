"""Queue worker entrypoint: rules, LLM, PDF (image: ape-worker).

A separate process and container from the API on purpose: if a report or an LLM
call exhausts memory, only this pod restarts. Agent WebSocket sessions live in
the API and are not affected, and the interrupted run goes back on the queue
once its lock is older than WORKER_STALE_AFTER_SECONDS.
"""

import asyncio
import signal

import structlog
from prometheus_client import start_http_server

from ape.analysis.pipeline import Pipeline
from ape.core.config import Settings, get_settings
from ape.core.db import Database
from ape.observability.logging import configure_logging
from ape.observability.metrics import Metrics
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
        await run_worker(Pipeline(settings, db, ObjectStore(settings), metrics), stop)
    finally:
        await db.close()
        log.info("worker_stopped")


def main() -> None:
    settings = get_settings()
    configure_logging("ape-worker", settings.log_level)
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
