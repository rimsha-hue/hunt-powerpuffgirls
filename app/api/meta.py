"""Public reference endpoints: client configuration, location and role suggestions."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AppSettings, Locations
from app.api.profile import place_out
from app.api.ratelimit import rate_limit
from app.api.schemas import Option, PlaceOut, PublicConfigOut
from app.domain.profile import LABELS
from app.domain.roles import suggest_roles

router = APIRouter(prefix="/api/meta", tags=["meta"])

Query_ = Annotated[str, Query(min_length=1, max_length=100)]


def _options(name: str) -> list[Option]:
    return [Option(value=value, label=label) for value, label in LABELS[name].items()]


@router.get("/config")
async def public_config(settings: AppSettings) -> PublicConfigOut:
    return PublicConfigOut(
        max_upload_mb=settings.max_upload_mb,
        google_sign_in=settings.google_enabled,
        retention_unverified_days=settings.retention_unverified_days,
        retention_email_log_days=settings.retention_email_log_days,
        support_email=settings.support_email,
        work_authorization=_options("work_authorization"),
        notice_period=_options("notice_period"),
        salary_period=_options("salary_period"),
    )


@router.get("/locations", dependencies=[Depends(rate_limit("lookup"))])
async def locations(q: Query_, index: Locations) -> list[PlaceOut]:
    return [place_out(place) for place in index.suggest(q)]


@router.get("/roles", dependencies=[Depends(rate_limit("lookup"))])
async def roles(q: Query_) -> list[str]:
    return suggest_roles(q)
