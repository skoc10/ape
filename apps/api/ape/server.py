"""FastAPI entrypoint: REST + agent WebSocket gateway (image: ape-api)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ape.core.auth import JWKSCache, JWKSFetcher, TokenVerifier, http_jwks_fetcher
from ape.core.config import Settings, get_settings
from ape.core.db import Database
from ape.core.tenancy import MembershipCache
from ape.gateway import endpoint as agent_endpoint
from ape.gateway.sessions import SessionRegistry
from ape.github.app import GitHubApp
from ape.observability.logging import configure_logging
from ape.observability.metrics import Metrics, install_metrics
from ape.observability.tracing import build_tracer_provider, instrument_app
from ape.routers import audits, clusters, health, me
from ape.routers import github as github_router
from ape.routers import settings as settings_router
from ape.storage import ObjectStore


def create_app(
    settings: Settings | None = None,
    *,
    jwks_fetcher: JWKSFetcher | None = None,
    github_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.service_name, settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(
            settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
        )
        github_http = httpx.AsyncClient(transport=github_transport, timeout=20.0)
        app.state.github = GitHubApp(settings, github_http)
        async with httpx.AsyncClient() as http, github_http:
            fetch = jwks_fetcher or http_jwks_fetcher(http, settings.jwks_url)
            app.state.db = db
            app.state.verifier = TokenVerifier(
                JWKSCache(fetch), issuer=settings.issuer, audience=settings.auth_audience
            )
            app.state.memberships = MembershipCache(db, ttl=settings.membership_cache_ttl_seconds)
            await db.open()
            try:
                yield
            finally:
                await db.close()
                tracer_provider.shutdown()

    app = FastAPI(title="APE Control Plane API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body repeats the rejected input, which may be a secret pasted
        # into the wrong field. Report where and why, never what.
        detail = [
            {
                "loc": [str(part) for part in error["loc"]],
                "type": error["type"],
                "msg": error["msg"],
            }
            for error in exc.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    metrics = Metrics()
    app.state.metrics = metrics
    app.state.settings = settings
    app.state.agent_sessions = SessionRegistry()
    app.state.object_store = ObjectStore(settings)
    install_metrics(app, metrics)

    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(clusters.router)
    app.include_router(audits.router)
    app.include_router(settings_router.router)
    app.include_router(github_router.router)
    app.include_router(agent_endpoint.router)

    tracer_provider = build_tracer_provider(
        settings.service_name, settings.otel_exporter_otlp_endpoint
    )
    instrument_app(app, tracer_provider)
    return app


def app() -> FastAPI:
    """ASGI factory for uvicorn: ``uvicorn ape.server:app --factory``."""
    return create_app()
