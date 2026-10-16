"""Application settings. Environments differ only by these values, never by code."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    log_json: bool = True

    # Required in production; the dev default is rejected when APP_ENV=production.
    secret_key: SecretStr = SecretStr("dev-only-insecure-key")

    database_url: str = "sqlite+aiosqlite:///./power_hunt.db"

    # Sessions and auth.
    session_days: int = Field(default=7, ge=1, le=30)
    # Google sign-in (optional). Redirect URI to register: {APP_BASE_URL}/api/auth/google/callback
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None

    # Demo account for judges. Credentials live only in env vars, never in the repo.
    demo_user_email: str | None = None
    demo_user_password: SecretStr | None = None
    demo_github_username: str | None = None

    # LLM. Model names are LiteLLM ids without the provider prefix for Groq.
    groq_api_key: SecretStr | None = None
    llm_primary_model: str = "llama-3.3-70b-versatile"
    llm_fallback_model: str = "llama-3.1-8b-instant"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_tokens: int = Field(default=2048, ge=64)

    max_upload_mb: int = Field(default=5, ge=1, le=20)

    # External services (later phases).
    rapidapi_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    github_token: SecretStr | None = None
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_app_password: SecretStr | None = None

    demo_mode: bool = False
    infer_threshold: float = Field(default=0.78, ge=0.0, le=1.0)
    scout_interval_hours: int = Field(default=6, ge=1)

    static_dir: Path = ROOT_DIR / "frontend" / "dist"

    @field_validator("database_url")
    @classmethod
    def _use_async_driver(cls, value: str) -> str:
        """Neon hands out `postgresql://` URLs; SQLAlchemy async needs an explicit driver."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        if value.startswith("sqlite:///"):
            return "sqlite+aiosqlite:///" + value[len("sqlite:///") :]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def google_enabled(self) -> bool:
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_client_secret.get_secret_value()
        )

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/api/auth/google/callback"

    def assert_safe_for_production(self) -> None:
        if self.is_production and self.secret_key.get_secret_value() == "dev-only-insecure-key":
            raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
