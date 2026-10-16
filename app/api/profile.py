"""Profile and onboarding preferences. Every value is validated and normalised before storage."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AppSettings, CurrentUser, Db, Locations
from app.api.schemas import PlaceOut, ProfileIn, ProfileOut, SalaryOut
from app.db.models import User
from app.domain.locations import LocationIndex, Place
from app.domain.profile import ProfileInvalidError, ProfileRules, normalize_changes
from app.services import audit

router = APIRouter(prefix="/api/profile", tags=["profile"])

USER_COLUMNS = frozenset({"name", "github_username", "portfolio_url"})


def place_out(place: Place) -> PlaceOut:
    return PlaceOut(key=place.key, label=place.label, kind=place.kind)


def profile_out(user: User, locations: LocationIndex) -> ProfileOut:
    p = user.profile
    home = locations.get(p.location) if p.location else None
    targets = [place for key in (p.target_locations or []) if (place := locations.get(key))]
    salary = (
        SalaryOut(amount=p.salary_amount, currency=p.salary_currency, period=p.salary_period)
        if p.salary_amount and p.salary_currency and p.salary_period
        else None
    )
    return ProfileOut(
        name=user.name,
        email=user.email,
        github_username=user.github_username,
        portfolio_url=user.portfolio_url,
        phone=p.phone,
        location=place_out(home) if home else None,
        linkedin=p.linkedin,
        work_authorization=p.work_authorization,
        notice_period=p.notice_period,
        salary=salary,
        target_roles=list(p.target_roles or []),
        target_locations=[place_out(place) for place in targets],
        match_threshold=p.match_threshold,
        calm_mode=p.calm_mode,
        resume_version=p.resume_version,
    )


@router.get("")
async def get_profile(principal: CurrentUser, locations: Locations) -> ProfileOut:
    return profile_out(principal.user, locations)


@router.put("")
async def update_profile(
    body: ProfileIn, principal: CurrentUser, db: Db, locations: Locations, settings: AppSettings
) -> ProfileOut:
    user = principal.user
    changes = body.model_dump(exclude_unset=True)
    try:
        values = normalize_changes(changes, ProfileRules(locations, settings.default_phone_region))
    except ProfileInvalidError as exc:
        # Same shape as FastAPI's own validation errors, one entry per field.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [
                {"type": "value_error", "loc": ["body", field], "msg": msg}
                for field, msg in exc.errors.items()
            ],
        ) from exc

    for column, value in values.items():
        setattr(user if column in USER_COLUMNS else user.profile, column, value)
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
    return profile_out(user, locations)
