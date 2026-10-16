"""Profile and onboarding preferences."""

from fastapi import APIRouter

from app.api.deps import CurrentUser, Db
from app.api.schemas import ProfileIn, ProfileOut
from app.db.models import User
from app.services import audit

router = APIRouter(prefix="/api/profile", tags=["profile"])

USER_FIELDS = ("name", "github_username", "portfolio_url")


def profile_out(user: User) -> ProfileOut:
    p = user.profile
    return ProfileOut(
        name=user.name,
        email=user.email,
        github_username=user.github_username,
        portfolio_url=user.portfolio_url,
        phone=p.phone,
        location=p.location,
        linkedin=p.linkedin,
        work_authorization=p.work_authorization,
        notice_period=p.notice_period,
        salary_expectation=p.salary_expectation,
        target_roles=list(p.target_roles or []),
        target_locations=list(p.target_locations or []),
        match_threshold=p.match_threshold,
        calm_mode=p.calm_mode,
        resume_version=p.resume_version,
    )


@router.get("")
async def get_profile(principal: CurrentUser) -> ProfileOut:
    return profile_out(principal.user)


@router.put("")
async def update_profile(body: ProfileIn, principal: CurrentUser, db: Db) -> ProfileOut:
    user = principal.user
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if key == "portfolio_url" and value is not None:
            value = str(value)
        if key in {"github_username", "portfolio_url"} and value == "":
            value = None
        setattr(user if key in USER_FIELDS else user.profile, key, value)
    await db.flush()
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="profile_updated",
        entity="profile",
        entity_id=user.id,
        fields=sorted(changes),
    )
    return profile_out(user)
