import io
import json
import logging

from fastapi.testclient import TestClient

from ape.core.config import Settings
from ape.server import create_app


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_checks_the_database(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_is_503_when_the_database_is_down(settings: Settings) -> None:
    broken = settings.model_copy(
        update={"database_url": "postgres://app_backend:x@127.0.0.1:1/ape?connect_timeout=1"}
    )
    app = create_app(broken)
    with TestClient(app) as client:
        app.state.db._pool.timeout = 0.5  # fail fast instead of waiting for a connection
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json() == {"status": "unavailable"}
        assert client.get("/healthz").status_code == 200


def test_metrics_exposes_http_histogram_with_route_templates(client: TestClient) -> None:
    client.get("/v1/me")  # 401, still measured
    client.get("/definitely/not/a/route/12345")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert (
        'ape_http_request_duration_seconds_count{method="GET",route="/v1/me",status="401"} 1.0'
        in body
    )
    assert 'route="unmatched",status="404"' in body
    assert "12345" not in body  # raw paths never become label values


def test_logs_are_json_with_trace_ids(client: TestClient) -> None:
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    stream = io.StringIO()
    original = handler.setStream(stream)
    try:
        client.get("/v1/me")
    finally:
        handler.setStream(original)

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    entry = next(line for line in lines if line.get("event") == "http_request")
    assert entry["route"] == "/v1/me"
    assert entry["status"] == 401
    assert entry["service"] == "ape-api"
    assert len(entry["trace_id"]) == 32
    assert len(entry["span_id"]) == 16
    assert entry["level"] == "info"
