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
    # Address browsers use for presigned download links, when it differs from the
    # in-cluster S3_ENDPOINT_URL (for example https://files.example.com).
    s3_public_endpoint_url: str = ""

    # Master key for masking placeholders; a per-tenant key is derived from it.
    masking_key: str = ""

    # GitHub App (pull requests with fixes). All empty = the feature is switched off.
    github_app_id: str = ""
    github_app_slug: str = ""
    github_app_private_key: str = ""  # PEM; literal "\\n" sequences are accepted
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"

    @property
    def github_configured(self) -> bool:
        return all(
            [
                self.github_app_id,
                self.github_app_slug,
                self.github_app_private_key,
                self.github_app_client_id,
                self.github_app_client_secret,
            ]
        )

    # LLM enrichment. Empty LLM_PROVIDER switches it off: reports are then built from the
    # rule engine and template texts only. "anthropic" talks to Claude (requires ZDR for
    # customer data); "openai_compatible" talks to vLLM, Ollama, LiteLLM or OpenAI through
    # LLM_BASE_URL.
    llm_provider: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_base_url: str = ""
    llm_api_key: str = ""
    # How the structured answer is requested: "native" uses the provider's JSON-schema
    # structured output (reliable on small local models, and needs no forced tool call,
    # which current Claude models reject); "tool" uses a tool call instead.
    llm_output_mode: str = "native"
    llm_batch_size: int = 25
    llm_max_tokens_per_run: int = 300_000
    # How many low findings of one rule are explained by the model; the rest of that rule
    # get the rule's template text. 0 sends every finding.
    llm_low_samples_per_rule: int = 3
    llm_request_timeout_seconds: float = 180.0

    # Worker (queue consumer for rules, LLM and reports).
    worker_poll_seconds: float = 2.0
    worker_stale_after_seconds: int = 600
    worker_max_attempts: int = 3
    worker_heartbeat_seconds: float = 60.0
    worker_metrics_port: int = 9091

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
