import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, TypeVar

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.config import ROOT_DIR, Settings
from app.domain.locations import LocationIndex, Place
from app.integrations.llm import LLMError
from app.main import create_app

T = TypeVar("T", bound=BaseModel)
PASSWORD = "correct-horse-battery"


def small_location_index() -> LocationIndex:
    """A few real places, including the traps the real data has (an alternate name shared by a
    bigger city, and cities that share a name across countries)."""

    def country(code: str, name: str, currency: str, aliases: list[str]) -> tuple[Place, list[str]]:
        place = Place(f"country:{code}", "country", name, code, name, 1_000_000, currency)
        return place, [code, *aliases]

    def city(
        gid: int, name: str, code: str, country_name: str, pop: int, aliases: list[str]
    ) -> tuple[Place, list[str]]:
        return Place(f"city:{gid}", "city", name, code, country_name, pop), aliases

    return LocationIndex.build(
        [
            country("PK", "Pakistan", "PKR", ["PAK"]),
            country("BD", "Bangladesh", "BDT", ["BGD"]),
            country("GB", "United Kingdom", "GBP", ["GBR"]),
            country("CA", "Canada", "CAD", ["CAN"]),
            country("US", "United States", "USD", ["USA"]),
            city(1176615, "Islamabad", "PK", "Pakistan", 601_600, ["ISB"]),
            city(1172451, "Lahore", "PK", "Pakistan", 6_310_888, []),
            city(1205733, "Chattogram", "BD", "Bangladesh", 2_581_643, ["Chittagong", "Islamabad"]),
            city(2643743, "London", "GB", "United Kingdom", 8_961_989, []),
            city(6058560, "London", "CA", "Canada", 346_765, []),
            city(5391959, "San Francisco", "US", "United States", 864_816, ["SF"]),
        ]
    )


LOCATIONS = small_location_index()


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
        scheduler_enabled=False,
        embeddings_enabled=False,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client(settings: Settings, fake_llm: FakeLLM) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=fake_llm, locations=LOCATIONS)) as test_client:
        yield test_client


VERIFY_LINK = re.compile(r"/api/auth/verify\?t=[\w-]+")


def signup(client: TestClient, email: str = "hero@example.com", *, verify: bool = True) -> str:
    """Creates an account, leaves the session cookie on the client, returns the CSRF token.

    With an outbox (a fake email provider), the verification email is taken out of it and its
    link followed, so tests start from a verified account and count only their own emails."""
    response = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD, "name": "Hero"}
    )
    assert response.status_code == 201, response.text
    token: str = response.json()["csrf_token"]
    outbox = getattr(client.app.state.email, "sent", None)  # type: ignore[attr-defined]
    if outbox:
        message = next(m for m in reversed(outbox) if VERIFY_LINK.search(m.text))
        outbox.remove(message)
        if verify:
            link = VERIFY_LINK.search(message.text)
            assert link is not None
            assert client.get(link.group(0), follow_redirects=False).status_code == 302
            # Verifying sends the welcome email; it isn't the test's own mail either.
            outbox[:] = [m for m in outbox if m.subject != "Welcome to Power Hunt"]
    return token


@pytest.fixture
def csrf(client: TestClient) -> str:
    return signup(client)
