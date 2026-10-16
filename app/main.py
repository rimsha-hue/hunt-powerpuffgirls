"""FastAPI entrypoint: API routers, security headers and the built React frontend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import health
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging
from app.security import SecurityHeadersMiddleware

logger = structlog.get_logger()

# Paths that belong to the backend; the SPA fallback must never shadow them.
BACKEND_PREFIXES = ("api/", "internal/", "mcp", "health", "ready", "docs", "openapi.json")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.assert_safe_for_production()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        logger.info("app_started", env=settings.app_env, version=__version__)
        yield
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
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.include_router(health.router)

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = settings.static_dir.resolve()
    index = dist / "index.html"
    if not index.is_file():
        logger.warning("frontend_not_built", path=str(dist))
        return

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(BACKEND_PREFIXES):
            raise HTTPException(status_code=404)
        candidate = (dist / path).resolve()
        # Serve real top-level files (favicon etc.), guarding against path traversal.
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
