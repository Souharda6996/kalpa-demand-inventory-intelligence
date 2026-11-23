"""Application settings loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://kalpa:kalpa@localhost:5432/kalpa"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000"
    mlflow_tracking_uri: str = "http://localhost:5000"
    environment: str = "dev"
    jwt_access_secret: str = "dev-only-access-secret-change-me-0123456789"  # noqa: S105  (dev default; rejected outside dev)
    jwt_refresh_secret: str = "dev-only-refresh-secret-change-me-012345678"  # noqa: S105  (dev default; rejected outside dev)
    access_ttl_minutes: int = 15
    refresh_ttl_days: int = 7
    cookie_secure: bool = False  # set COOKIE_SECURE=true behind HTTPS (production)
    login_max_failures: int = 5
    login_lockout_seconds: int = 900
    signup_per_hour: int = 20  # per client IP
    copilot_per_minute: int = 30  # per user (each question may cost an LLM call)
    uploads_per_hour: int = 60  # per user
    demo_loads_per_hour: int = 6  # per user (each load writes ~220k rows)
    db_pool_size: int = 10  # connections kept open by the API process
    db_max_overflow: int = 20  # extra connections allowed during bursts
    inline_jobs: bool = False  # run jobs in the API process (dev/tests) instead of the ARQ worker
    model_dir: str = "data/models"  # fitted models for what-if scenarios (shared by API and worker)
    mlflow_db_uri: str = "sqlite:///data/mlflow.db"
    anthropic_api_key: str = ""  # optional: without it the narrative is a template and the copilot is rule-based
    copilot_model: str = "claude-haiku-5-5"
    upload_dir: str = "data/uploads"
    max_upload_bytes: int = 50_000_000
    git_sha: str = "dev"
    app_version: str = "0.1.0"

    @property
    def cors_origin_list(self) -> list[str]:
        """Split the comma-separated CORS allow-list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return cached settings."""
    return Settings()


def assert_safe_secrets(settings: Settings) -> None:
    """Refuse to start outside dev with built-in defaults or an insecure cookie/JWT configuration."""
    if settings.environment == "dev":
        return
    problems: list[str] = []
    if "dev-only" in settings.jwt_access_secret or "dev-only" in settings.jwt_refresh_secret:
        problems.append("set JWT_ACCESS_SECRET / JWT_REFRESH_SECRET (the built-in dev secrets are public)")
    elif len(settings.jwt_access_secret) < 32 or len(settings.jwt_refresh_secret) < 32:
        problems.append("JWT secrets must be at least 32 characters")
    elif settings.jwt_access_secret == settings.jwt_refresh_secret:
        problems.append("JWT_ACCESS_SECRET and JWT_REFRESH_SECRET must differ")
    if "kalpa:kalpa@" in settings.database_url:
        problems.append("set DATABASE_URL (the default kalpa:kalpa password is public)")
    if not settings.cookie_secure:
        problems.append("set COOKIE_SECURE=true (session cookies must only travel over HTTPS)")
    if problems:
        raise RuntimeError("Unsafe configuration outside dev: " + "; ".join(problems))
