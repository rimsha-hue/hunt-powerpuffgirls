"""Cross-cutting behaviour: profile validation contract, reference endpoints, rate limits,
request size limits and caching headers."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import LOCATIONS, FakeLLM


class TestProfileValidation:
    def test_invalid_fields_come_back_as_field_errors_and_nothing_is_saved(
        self, client: TestClient, csrf: str
    ) -> None:
        headers = {"X-CSRF-Token": csrf}
        client.put("/api/profile", json={"target_roles": ["Data Analyst"]}, headers=headers)

        response = client.put(
            "/api/profile",
            json={"target_roles": ["ML Engineer"], "target_locations": ["bla bla bla"]},
            headers=headers,
        )
        assert response.status_code == 422
        assert [e["loc"] for e in response.json()["detail"]] == [["body", "target_locations"]]
        assert client.get("/api/profile").json()["target_roles"] == ["Data Analyst"]  # untouched

    def test_locations_are_returned_as_places(self, client: TestClient, csrf: str) -> None:
        body = client.put(
            "/api/profile",
            json={"target_locations": ["wfh", "ISB", "Lahore, Pakistan"]},
            headers={"X-CSRF-Token": csrf},
        ).json()
        assert [p["label"] for p in body["target_locations"]] == [
            "Remote",
            "Islamabad, Pakistan",
            "Lahore, Pakistan",
        ]

    def test_unknown_fields_are_rejected(self, client: TestClient, csrf: str) -> None:
        response = client.put(
            "/api/profile", json={"is_admin": True}, headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 422

    def test_enums_are_enforced(self, client: TestClient, csrf: str) -> None:
        response = client.put(
            "/api/profile", json={"notice_period": "whenever"}, headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 422


class TestReferenceEndpoints:
    def test_config_publishes_limits_and_choices(self, client: TestClient) -> None:
        config = client.get("/api/meta/config").json()
        assert config["max_upload_mb"] == 5
        assert {o["value"] for o in config["notice_period"]} >= {"immediate", "1_month"}

    def test_location_and_role_suggestions(self, client: TestClient) -> None:
        assert (
            client.get("/api/meta/locations", params={"q": "lah"}).json()[0]["label"]
            == "Lahore, Pakistan"
        )
        assert "Data Analyst" in client.get("/api/meta/roles", params={"q": "data"}).json()
        assert client.get("/api/meta/locations", params={"q": "x" * 101}).status_code == 422


@pytest.fixture
def strict_client(settings: Settings) -> Iterator[TestClient]:
    tight = settings.model_copy(update={"rate_limit_lookup": "3/minute", "max_json_kb": 4})
    with TestClient(create_app(tight, llm=FakeLLM(), locations=LOCATIONS)) as client:
        yield client


class TestProtections:
    def test_rate_limits_come_from_settings_and_say_when_to_retry(
        self, strict_client: TestClient
    ) -> None:
        responses = [strict_client.get("/api/meta/roles", params={"q": "data"}) for _ in range(4)]
        assert [r.status_code for r in responses] == [200, 200, 200, 429]
        assert int(responses[-1].headers["retry-after"]) > 0

    def test_oversized_json_bodies_are_refused(self, strict_client: TestClient) -> None:
        response = strict_client.post(
            "/api/auth/login",
            content=b'{"email": "a@b.co", "password": "' + b"x" * 8000 + b'"}',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413

    def test_oversized_body_without_content_length_is_refused(
        self, strict_client: TestClient
    ) -> None:
        def chunks() -> Iterator[bytes]:
            yield b'{"email": "a@b.co", "password": "'
            for _ in range(10):
                yield b"x" * 1000
            yield b'"}'

        response = strict_client.post(
            "/api/auth/login", content=chunks(), headers={"content-type": "application/json"}
        )
        assert response.status_code == 413

    def test_cache_headers(self, client: TestClient) -> None:
        assert (
            client.get("/assets/app.js").headers["cache-control"]
            == "public, max-age=31536000, immutable"
        )
        assert client.get("/").headers["cache-control"] == "no-cache"
        assert client.get("/favicon.svg").headers["cache-control"] == "public, max-age=86400"
