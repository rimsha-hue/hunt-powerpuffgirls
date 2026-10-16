"""Activity screen: the audit timeline and the human-gate counters."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, Db
from app.api.schemas import ActivityEventOut, ActivityOut, AskedOut, CountersOut
from app.domain.activity import GATES, label
from app.services.activity import counters, questions_by_application, timeline

router = APIRouter(prefix="/api", tags=["activity"])

# Applications shown on the "questions asked" trend.
TREND_APPLICATIONS = 10


@router.get("/activity")
async def activity(
    principal: CurrentUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ActivityOut:
    user_id = principal.user.id
    events = await timeline(db, user_id, limit)
    return ActivityOut(
        counters=CountersOut(**asdict(await counters(db, user_id))),
        questions=[
            AskedOut(**asdict(a))
            for a in await questions_by_application(db, user_id, TREND_APPLICATIONS)
        ],
        events=[
            ActivityEventOut(
                id=e.id,
                actor=e.actor,
                action=e.action,
                label=label(e.action),
                gate=GATES.get(e.action),
                entity=e.entity,
                created_at=e.created_at,
            )
            for e in events
        ],
    )
