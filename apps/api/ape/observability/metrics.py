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
