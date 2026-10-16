"""Request dependencies: database session, current user, CSRF enforcement, shared services."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import AuthSession, User
from app.domain.locations import LocationIndex
from app.domain.taxonomy import Taxonomy
from app.integrations.fetch import SafeFetcher
from app.integrations.github import GitHubClient
from app.integrations.llm import StructuredLLM
from app.services.auth import resolve_session, tokens_match

SESSION_COOKIE = "ph_session"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_llm(request: Request) -> StructuredLLM:
    llm: StructuredLLM = request.app.state.llm
    return llm


def get_taxonomy(request: Request) -> Taxonomy:
    taxonomy: Taxonomy = request.app.state.taxonomy
    return taxonomy


def get_github(request: Request) -> GitHubClient:
    client: GitHubClient = request.app.state.github
    return client


def get_fetcher(request: Request) -> SafeFetcher:
    fetcher: SafeFetcher = request.app.state.fetcher
    return fetcher


async def get_locations(request: Request) -> LocationIndex:
    """The location index, waiting for the background load on the first requests after start."""
    index: LocationIndex = await request.app.state.locations
    return index


Db = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
LLM = Annotated[StructuredLLM, Depends(get_llm)]
Tax = Annotated[Taxonomy, Depends(get_taxonomy)]
GitHub = Annotated[GitHubClient, Depends(get_github)]
Fetcher = Annotated[SafeFetcher, Depends(get_fetcher)]
Locations = Annotated[LocationIndex, Depends(get_locations)]


@dataclass
class Principal:
    user: User
    session: AuthSession


async def get_principal(request: Request, db: Db) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    resolved = await resolve_session(db, token) if token else None
    if resolved is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    session, user = resolved
    if request.method in UNSAFE_METHODS and not tokens_match(
        session.csrf_token, request.headers.get(CSRF_HEADER)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token.")
    return Principal(user=user, session=session)


async def get_full_principal(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """A session minted from a signed Apply link is limited to that job (Phase 2)."""
    if principal.session.scope_job_id is not None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This link only opens one job. Log in for full access."
        )
    return principal


CurrentUser = Annotated[Principal, Depends(get_full_principal)]
