"""FastAPI entrypoint: API routers, middleware and the built React frontend."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import httpx
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app import __version__
from app.api import activity, applications, auth, forms, health, jobs, ledger, meta, plans, profile
from app.api.ratelimit import RateLimiter
from app.assets import RECORDED_JOBS
from app.config import Settings, get_settings
from app.db.session import DatabaseProbe, create_engine, create_session_factory, ping
from app.domain.locations import LocationIndex, load_geonames
from app.domain.taxonomy import default_taxonomy
from app.integrations.email import EmailProvider, NullEmailProvider, SmtpEmailProvider
from app.integrations.embeddings import EmbeddingSimilarity, LexicalSimilarity, Similarity
from app.integrations.fetch import SafeFetcher
from app.integrations.github import GitHubClient
from app.integrations.jobs.base import JobSource
from app.integrations.jobs.sources import (
    ArbeitnowSource,
    JSearchSource,
    RecordedSource,
    RemotiveSource,
)
from app.integrations.llm import StructuredLLM, build_llm
from app.integrations.research import TavilyClient
from app.logging import configure_logging
from app.middleware import BodySizeLimitMiddleware
from app.security import SecurityHeadersMiddleware
from app.services.runner import ScoutRunner, ScoutScheduler
from app.services.scout import ScoutSources

logger = structlog.get_logger()

# Paths that belong to the backend; the SPA fallback must never shadow them.
BACKEND_PREFIXES = (
    "api/",
    "apply/",
    "internal/",
    "mcp",
    "health",
    "ready",
    "docs",
    "openapi.json",
)
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"  # content-hashed build assets
CACHE_PUBLIC_FILES = "public, max-age=86400"  # media, icons
CACHE_HTML = "no-cache"  # always revalidate the app shell so deploys show up at once


class ImmutableStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = CACHE_IMMUTABLE
        return response


def build_job_sources(settings: Settings, http: httpx.AsyncClient) -> ScoutSources:
    primary: list[JobSource] = []
    if settings.rapidapi_key and settings.rapidapi_key.get_secret_value():
        primary.append(
            JSearchSource(
                http,
                api_url=str(settings.jsearch_api_url),
                api_key=settings.rapidapi_key.get_secret_value(),
            )
        )
    return ScoutSources(
        primary=primary,
        secondary=[
            RemotiveSource(http, api_url=str(settings.remotive_api_url)),
            ArbeitnowSource(http, api_url=str(settings.arbeitnow_api_url)),
        ],
        recorded=RecordedSource(RECORDED_JOBS),
    )


def build_email(settings: Settings) -> EmailProvider:
    if not settings.email_enabled or not settings.smtp_user or not settings.smtp_app_password:
        return NullEmailProvider()
    return SmtpEmailProvider(
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        password=settings.smtp_app_password.get_secret_value(),
        timeout=settings.smtp_timeout_seconds,
        redirect_to=settings.email_redirect_to,
    )


async def _load_locations() -> LocationIndex:
    index = await anyio.to_thread.run_sync(load_geonames)
    logger.info("locations_loaded", places=len(index.places))
    return index


def create_app(
    settings: Settings | None = None,
    llm: StructuredLLM | None = None,
    fetcher: SafeFetcher | None = None,
    locations: LocationIndex | None = None,
    job_sources: ScoutSources | None = None,
    email: EmailProvider | None = None,
    similarity: Similarity | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            connect_timeout=settings.db_connect_timeout_seconds,
        )
        app.state.db_probe = DatabaseProbe(lambda: ping(engine))
        app.state.db_probe.start()  # warm the pool now, so the first request doesn't pay for it
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_seconds), follow_redirects=False
        )
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.http = http
        app.state.github = GitHubClient(
            http,
            api_url=str(settings.github_api_url),
            token=settings.github_token.get_secret_value() if settings.github_token else None,
            max_repos=settings.github_max_repos,
            language_detail_repos=settings.github_language_detail_repos,
        )
        app.state.fetcher = fetcher or SafeFetcher(
            http,
            max_bytes=settings.fetch_max_bytes,
            timeout=settings.http_timeout_seconds,
            max_redirects=settings.fetch_max_redirects,
            user_agent=f"PowerHuntBot/{__version__} (+{settings.base_url})",
        )
        # The GeoNames index takes seconds to build; load it in the background so the app is
        # ready at once. Location lookups await it; nothing else depends on it.
        if locations is not None:
            ready: asyncio.Future[LocationIndex] = asyncio.get_running_loop().create_future()
            ready.set_result(locations)
            app.state.locations = ready
        else:
            app.state.locations = asyncio.create_task(_load_locations())

        async def location_index() -> LocationIndex:
            index: LocationIndex = await app.state.locations
            return index

        app.state.email = email or build_email(settings)
        app.state.tavily = (
            TavilyClient(
                http,
                api_url=str(settings.tavily_api_url),
                api_key=settings.tavily_api_key.get_secret_value(),
                max_results=settings.research_max_results,
            )
            if settings.tavily_api_key and settings.tavily_api_key.get_secret_value()
            else None
        )
        app.state.scout_runner = ScoutRunner(
            session_factory=app.state.session_factory,
            sources=job_sources or build_job_sources(settings, http),
            email=app.state.email,
            taxonomy=app.state.taxonomy,
            locations=location_index,
            settings=settings,
        )
        # The embedding model loads in the background; until then the Verifier uses the lexical
        # similarity, which only ever sends more claims to the user.
        loading = None
        if isinstance(app.state.similarity, EmbeddingSimilarity):
            loading = asyncio.create_task(app.state.similarity.load())
        app.state.scheduler = ScoutScheduler(app.state.scout_runner, settings.scout_interval_hours)
        if settings.scheduler_enabled:
            app.state.scheduler.start()
        logger.info(
            "app_started", env=settings.app_env, version=__version__, llm=app.state.llm.configured
        )
        yield
        app.state.scheduler.shutdown()
        if loading is not None and not loading.done():
            loading.cancel()
        if isinstance(app.state.locations, asyncio.Task) and not app.state.locations.done():
            app.state.locations.cancel()
        await app.state.db_probe.close()
        await http.aclose()
        await engine.dispose()
        logger.info("app_stopped")

    app = FastAPI(
        title="Power Hunt",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.taxonomy = default_taxonomy()
    app.state.llm = llm or build_llm(settings)
    app.state.similarity = similarity or (
        EmbeddingSimilarity(settings.embedding_model, settings.embedding_cache_dir)
        if settings.embeddings_enabled
        else LexicalSimilarity()
    )
    app.state.rate_limiter = RateLimiter(settings.rate_limit_storage_uri, settings.rate_limits)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(
        BodySizeLimitMiddleware,
        json_limit_bytes=settings.max_json_kb * 1024,
        upload_limit_bytes=settings.max_upload_mb * 1024 * 1024,
    )

    routers = (
        health.router,
        auth.router,
        profile.router,
        ledger.router,
        meta.router,
        jobs.router,
        applications.router,
        forms.router,
        plans.router,
        activity.router,
    )
    for router in routers:
        app.include_router(router)

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = settings.static_dir.resolve()
    index = dist / "index.html"
    if not index.is_file():
        logger.warning("frontend_not_built", path=str(dist))
        return

    if (dist / "assets").is_dir():
        app.mount("/assets", ImmutableStaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(BACKEND_PREFIXES):
            raise HTTPException(status_code=404)
        candidate = (dist / path).resolve()
        # Serve real files (favicon, media), guarding against path traversal.
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate, headers={"Cache-Control": CACHE_PUBLIC_FILES})
        return FileResponse(index, headers={"Cache-Control": CACHE_HTML})


app = create_app()
