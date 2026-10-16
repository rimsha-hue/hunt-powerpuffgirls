from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from tests.conftest import LOCATIONS


class TestHealth:
    def test_health_reports_each_dependency(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["llm"] == "not_configured"
        assert body["scheduler"] == "not_started"

    def test_health_is_degraded_when_database_is_unreachable(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        bad = settings.model_copy(
            update={"database_url": f"sqlite+aiosqlite:///{tmp_path}/missing/dir/x.db"}
        )
        with TestClient(create_app(bad, locations=LOCATIONS)) as client:
            health = client.get("/health")
            ready = client.get("/ready")
        assert health.status_code == 503
        assert health.json()["database"] == "down"
        assert ready.status_code == 503

    def test_ready_returns_ok(self, client: TestClient) -> None:
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


class TestSecurityHeaders:
    @pytest.mark.parametrize("path", ["/health", "/", "/jobs"])
    def test_every_response_carries_security_headers(self, client: TestClient, path: str) -> None:
        headers = client.get(path).headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


class TestFrontend:
    def test_root_serves_the_spa(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "Power Hunt" in response.text

    def test_client_routes_fall_back_to_index(self, client: TestClient) -> None:
        response = client.get("/apply/42")
        assert response.status_code == 200
        assert "Power Hunt" in response.text

    def test_static_assets_are_served(self, client: TestClient) -> None:
        assert client.get("/assets/app.js").text == "console.log('ok')"
        assert client.get("/favicon.svg").status_code == 200

    def test_unknown_api_paths_are_404_not_the_spa(self, client: TestClient) -> None:
        assert client.get("/api/nope").status_code == 404

    def test_path_traversal_does_not_escape_dist(self, client: TestClient) -> None:
        response = client.get("/..%2f..%2fpyproject.toml")
        assert "[project]" not in response.text


class TestSettings:
    def test_neon_url_gets_async_driver(self) -> None:
        settings = Settings(database_url="postgresql://u:p@host/db?sslmode=require", _env_file=None)  # type: ignore[call-arg]
        assert settings.database_url == "postgresql+psycopg://u:p@host/db?sslmode=require"

    @pytest.mark.parametrize("secret", [None, "short-secret"])
    def test_production_refuses_missing_or_weak_secrets(self, secret: str | None) -> None:
        extra = {"secret_key": SecretStr(secret)} if secret else {}
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            Settings(app_env="production", _env_file=None, **extra)  # type: ignore[call-arg, arg-type]

    def test_production_accepts_a_real_secret(self) -> None:
        settings = Settings(app_env="production", secret_key=SecretStr("x" * 48), _env_file=None)  # type: ignore[call-arg]
        assert settings.is_production

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("app_env", "prod"),
            ("log_level", "LOUD"),
            ("app_base_url", "not a url"),
            ("rate_limit_login", "lots"),
            ("jina_reader_url", "http://insecure.example/"),
            ("default_phone_region", "pakistan"),
            ("max_upload_mb", 500),
        ],
    )
    def test_misconfiguration_fails_at_startup(self, field: str, value: object) -> None:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{field: value})  # type: ignore[call-arg, arg-type]
