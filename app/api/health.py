"""Health endpoints: `/health` for humans and monitors, `/ready` for the platform."""

import asyncio
from typing import Literal

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app import __version__
from app.db.session import ping

router = APIRouter(tags=["health"])
logger = structlog.get_logger()

DB_PING_TIMEOUT_SECONDS = 3.0

CheckState = Literal["ok", "down", "not_configured", "not_started"]


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: CheckState
    llm: CheckState
    scheduler: CheckState


async def _database_state(request: Request) -> CheckState:
    try:
        await asyncio.wait_for(ping(request.app.state.engine), DB_PING_TIMEOUT_SECONDS)
        return "ok"
    except Exception as exc:
        logger.warning("health_db_unreachable", error_type=type(exc).__name__)
        return "down"


@router.get("/health", response_model=HealthReport)
async def health(request: Request, response: Response) -> HealthReport:
    settings = request.app.state.settings
    database = await _database_state(request)
    # The LLM client and scheduler arrive in later phases; report honestly until then.
    llm: CheckState = "not_started" if settings.groq_api_key else "not_configured"
    report = HealthReport(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        database=database,
        llm=llm,
        scheduler="not_started",
    )
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, str]:
    if await _database_state(request) != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready"}
    return {"status": "ready"}
