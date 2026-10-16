from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, TypeVar

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.deps import limiter
from app.config import ROOT_DIR, Settings
from app.integrations.llm import LLMError
from app.main import create_app

T = TypeVar("T", bound=BaseModel)
PASSWORD = "correct-horse-battery"


class FakeLLM:
    """Returns a canned payload (validated against the requested schema) or raises."""

    def __init__(
        self, payload: dict[str, Any] | None = None, error: LLMError | None = None
    ) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[Mapping[str, str]] = []

    @property
    def configured(self) -> bool:
        return self.payload is not None or self.error is not None

    async def generate(
        self, *, schema: type[T], system: str, task: str, data: Mapping[str, str]
    ) -> T:
        self.calls.append(data)
        if self.error:
            raise self.error
        return schema.model_validate(self.payload)


def migrate(database_url: str) -> None:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> None:
    limiter.reset()


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Power Hunt</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    migrate(url)
    return url


@pytest.fixture
def settings(database_url: str, static_dir: Path) -> Settings:
    return Settings(
        database_url=database_url,
        static_dir=static_dir,
        log_json=False,
        demo_user_email="demo@example.com",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client(settings: Settings, fake_llm: FakeLLM) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=fake_llm)) as test_client:
        yield test_client


def signup(client: TestClient, email: str = "hero@example.com") -> str:
    """Creates an account, leaves the session cookie on the client, returns the CSRF token."""
    response = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD, "name": "Hero"}
    )
    assert response.status_code == 201, response.text
    token: str = response.json()["csrf_token"]
    return token


@pytest.fixture
def csrf(client: TestClient) -> str:
    return signup(client)
