"""Prometheus metrics and the HTTP middleware that records them."""

import time

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import RequestResponseEndpoint

log = structlog.get_logger("ape.http")

_QUIET_PATHS = {"/healthz", "/readyz", "/metrics"}


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.http_request_duration = Histogram(
            "ape_http_request_duration_seconds",
            "HTTP request latency by method, route template and status code.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.agent_sessions = Gauge(
            "ape_agent_sessions",
            "Open agent WebSocket sessions.",
            registry=self.registry,
        )
        self.audit_runs = Counter(
            "ape_audit_runs_total",
            "Audit runs finished by the worker, by final status.",
            ["status"],
            registry=self.registry,
        )
        self.audit_stage_seconds = Histogram(
            "ape_audit_stage_seconds",
            "Time spent per pipeline stage.",
            ["stage"],
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
            registry=self.registry,
        )
        self.llm_tokens = Counter(
            "ape_llm_tokens_total",
            "LLM tokens used, by model and direction (input or output).",
            ["model", "direction"],
            registry=self.registry,
        )
        self.pdf_render_seconds = Histogram(
            "ape_pdf_render_seconds",
            "Time to render a report PDF.",
            buckets=(0.5, 1, 2.5, 5, 10, 30, 60),
            registry=self.registry,
        )
        self.masking_redactions = Counter(
            "ape_masking_redactions_total",
            "Values replaced by a placeholder, by detector.",
            ["detector"],
            registry=self.registry,
        )
        self.agent_messages = Counter(
            "ape_agent_messages_total",
            "Agent protocol messages by type and direction (in: from agent, out: to agent).",
            ["type", "direction"],
            registry=self.registry,
        )


def _route_template(request: Request) -> str:
    # The route template (e.g. /v1/clusters/{cluster_id}), never the raw path:
    # keeps label cardinality bounded and identifiers out of metrics and logs.
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def install_metrics(app: FastAPI, metrics: Metrics) -> None:
    @app.middleware("http")
    async def record(request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = _route_template(request)
            metrics.http_request_duration.labels(request.method, route, str(status_code)).observe(
                elapsed
            )
            if route not in _QUIET_PATHS:
                log.info(
                    "http_request",
                    method=request.method,
                    route=route,
                    status=status_code,
                    duration_ms=round(elapsed * 1000, 2),
                )

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)
