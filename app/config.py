"""Application settings. Environments differ only by these values, never by code.

Rule of thumb for what lives here: anything that changes between environments or deployments
(endpoints, credentials, limits, timeouts, capacities) is a setting. Domain rules (what counts as
evidence, how a score is computed) are deliberate, tested code, not configuration.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from limits import parse_many
from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DEV_SECRET = "dev-only-insecure-key"  # noqa: S105 (a sentinel that production refuses)
MIN_PRODUCTION_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # --- Core ---
    app_env: Literal["development", "test", "production"] = "development"
    app_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    secret_key: SecretStr = SecretStr(DEV_SECRET)

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./power_hunt.db"
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    # Opening a connection can take seconds against a serverless database far away (Neon wakes
    # from idle); the health check waits this long before reporting the database as down.
    db_connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    db_health_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # --- Sessions and sign-in ---
    session_days: int = Field(default=7, ge=1, le=30)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None

    # --- Rate limits ("N/period", several separated by ";"). Storage: memory:// for one
    # instance; a shared store (redis://...) when running more than one worker or replica.
    rate_limit_storage_uri: str = "memory://"
    rate_limit_login: str = "10/minute"
    rate_limit_signup: str = "5/minute"
    rate_limit_oauth: str = "20/minute"
    rate_limit_ingest: str = "10/minute"
    rate_limit_lookup: str = "120/minute"

    # --- Request limits ---
    max_upload_mb: int = Field(default=5, ge=1, le=20)
    max_json_kb: int = Field(default=64, ge=4, le=1024)

    # --- Demo account for judges (credentials only ever come from the environment) ---
    demo_user_email: str | None = None
    demo_user_password: SecretStr | None = None
    demo_github_username: str | None = None

    # --- LLM (LiteLLM ids; Groq models without the provider prefix) ---
    groq_api_key: SecretStr | None = None
    llm_primary_model: str = "llama-3.3-70b-versatile"
    llm_fallback_model: str = "llama-3.1-8b-instant"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_max_tokens: int = Field(default=2048, ge=64, le=8192)

    # --- Outbound HTTP ---
    http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    fetch_max_bytes: int = Field(default=2 * 1024 * 1024, ge=64 * 1024, le=20 * 1024 * 1024)
    fetch_max_redirects: int = Field(default=5, ge=0, le=10)
    # Renders JavaScript pages for us. Empty disables the fallback.
    jina_reader_url: str = "https://r.jina.ai/"
    github_api_url: AnyHttpUrl = AnyHttpUrl("https://api.github.com")
    github_token: SecretStr | None = None
    github_max_repos: int = Field(default=30, ge=1, le=100)
    github_language_detail_repos: int = Field(default=10, ge=0, le=100)

    # --- Input interpretation ---
    # Region used to read phone numbers written without a country code (ISO 3166 alpha-2).
    default_phone_region: str = Field(default="PK", pattern=r"^[A-Z]{2}$")

    # --- Later phases ---
    rapidapi_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str | None = None
    smtp_app_password: SecretStr | None = None
    demo_mode: bool = False
    infer_threshold: float = Field(default=0.78, ge=0.0, le=1.0)
    scout_interval_hours: int = Field(default=6, ge=1, le=168)

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

    @field_validator(
        "rate_limit_login",
        "rate_limit_signup",
        "rate_limit_oauth",
        "rate_limit_ingest",
        "rate_limit_lookup",
    )
    @classmethod
    def _valid_rate_limit(cls, value: str) -> str:
        try:
            parse_many(value)
        except ValueError as exc:
            raise ValueError(f"not a rate limit such as '10/minute': {value!r}") from exc
        return value

    @field_validator("jina_reader_url")
    @classmethod
    def _jina_url(cls, value: str) -> str:
        if value and not value.startswith("https://"):
            raise ValueError("JINA_READER_URL must be an https URL or empty")
        return value

    @model_validator(mode="after")
    def _production_rules(self) -> "Settings":
        if self.app_env == "production":
            secret = self.secret_key.get_secret_value()
            if secret == DEV_SECRET or len(secret) < MIN_PRODUCTION_SECRET_LENGTH:
                raise ValueError(
                    f"SECRET_KEY must be set to at least {MIN_PRODUCTION_SECRET_LENGTH} random "
                    "characters when APP_ENV=production"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def base_url(self) -> str:
        return str(self.app_base_url).rstrip("/")

    @property
    def google_enabled(self) -> bool:
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_client_secret.get_secret_value()
        )

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.base_url}/api/auth/google/callback"

    @property
    def rate_limits(self) -> dict[str, str]:
        return {
            "login": self.rate_limit_login,
            "signup": self.rate_limit_signup,
            "oauth": self.rate_limit_oauth,
            "ingest": self.rate_limit_ingest,
            "lookup": self.rate_limit_lookup,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
