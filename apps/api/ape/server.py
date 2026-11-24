"""FastAPI entrypoint: REST + agent WebSocket gateway (image: ape-api)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from ape.core.auth import JWKSCache, JWKSFetcher, TokenVerifier, http_jwks_fetcher
from ape.core.config import Settings, get_settings
from ape.core.db import Database
from ape.core.tenancy import MembershipCache
from ape.observability.logging import configure_logging
from ape.observability.metrics import Metrics, install_metrics
from ape.observability.tracing import build_tracer_provider, instrument_app
from ape.routers import health, me


def create_app(
    settings: Settings | None = None, *, jwks_fetcher: JWKSFetcher | None = None
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
        async with httpx.AsyncClient() as http:
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

    metrics = Metrics()
    app.state.metrics = metrics
    install_metrics(app, metrics)

    app.include_router(health.router)
    app.include_router(me.router)

    tracer_provider = build_tracer_provider(
        settings.service_name, settings.otel_exporter_otlp_endpoint
    )
    instrument_app(app, tracer_provider)
    return app


def app() -> FastAPI:
    """ASGI factory for uvicorn: ``uvicorn ape.server:app --factory``."""
    return create_app()
