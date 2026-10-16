"""Health endpoints: `/health` for humans and monitors, `/ready` for the platform."""

from typing import Literal

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app import __version__
from app.db.session import DatabaseProbe

router = APIRouter(tags=["health"])
logger = structlog.get_logger()

CheckState = Literal["ok", "down", "configured", "not_configured", "running", "not_started"]


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: CheckState
    llm: CheckState
    scheduler: CheckState


async def _database_state(request: Request) -> CheckState:
    probe: DatabaseProbe = request.app.state.db_probe
    problem = await probe.check(request.app.state.settings.db_health_timeout_seconds)
    if problem is None:
        return "ok"
    logger.warning("health_db_unreachable", reason=problem)
    return "down"


@router.get("/health", response_model=HealthReport)
async def health(request: Request, response: Response) -> HealthReport:
    database = await _database_state(request)
    # No live LLM call here: probing a free-tier quota on every health check would waste it.
    llm: CheckState = "configured" if request.app.state.llm.configured else "not_configured"
    report = HealthReport(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        database=database,
        llm=llm,
        scheduler="running" if request.app.state.scheduler.running else "not_started",
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
