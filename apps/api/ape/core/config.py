"""Runtime configuration, read from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "ape-api"
    log_level: str = "INFO"

    # PostgreSQL as the app_backend role (never a BYPASSRLS role).
    database_url: str = ""
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    # Better Auth issues the JWTs; the API only verifies them through JWKS.
    better_auth_url: str = "http://localhost:3000"
    auth_jwks_url: str = ""
    auth_audience: str = "ape-api"
    membership_cache_ttl_seconds: float = 60.0

    # Agent gateway (protocol section 3 of mvp-plan.md).
    agent_heartbeat_seconds: int = 30
    agent_hello_timeout_seconds: float = 10.0
    agent_max_pods: int = 5000
    agent_max_compressed_bytes: int = 50 * 1024 * 1024

    # S3-compatible object storage (SeaweedFS locally). Only the S3 API is used.
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_snapshots: str = "snapshots"
    s3_bucket_reports: str = "reports"

    # Master key for masking placeholders; a per-tenant key is derived from it.
    masking_key: str = ""

    # Tracing is off unless an OTLP endpoint is configured.
    otel_exporter_otlp_endpoint: str = ""

    @property
    def jwks_url(self) -> str:
        return self.auth_jwks_url or f"{self.better_auth_url.rstrip('/')}/api/auth/jwks"

    @property
    def issuer(self) -> str:
        return self.better_auth_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
