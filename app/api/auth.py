"""Signup, login, logout, current user and "Delete my data"."""

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete

from app.api.deps import SESSION_COOKIE, AppSettings, CurrentUser, Db, limiter
from app.api.schemas import LoginIn, MeOut, SignupIn, UserOut
from app.db.models import User
from app.services import audit
from app.services.auth import (
    authenticate,
    create_session,
    create_user,
    end_session,
    get_user_by_email,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = structlog.get_logger()


def _set_cookie(response: Response, token: str, settings: AppSettings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def me_out(user: User, csrf_token: str, settings: AppSettings) -> MeOut:
    return MeOut(
        user=UserOut.model_validate(user),
        csrf_token=csrf_token,
        onboarded=user.profile.resume_version > 0,
        calm_mode=user.profile.calm_mode,
        is_demo=bool(settings.demo_user_email)
        and user.email == (settings.demo_user_email or "").lower(),
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def signup(
    request: Request, body: SignupIn, response: Response, db: Db, settings: AppSettings
) -> MeOut:
    if await get_user_by_email(db, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists.")
    user = await create_user(db, email=body.email, password=body.password, name=body.name)
    token, session = await create_session(db, user, days=settings.session_days)
    audit.record(
        db, user_id=user.id, actor="user", action="signed_up", entity="user", entity_id=user.id
    )
    _set_cookie(response, token, settings)
    return me_out(user, session.csrf_token, settings)


@router.post("/login")
@limiter.limit("10/minute")
async def login(
    request: Request, body: LoginIn, response: Response, db: Db, settings: AppSettings
) -> MeOut:
    user = await authenticate(db, body.email, body.password)
    if user is None:
        logger.info("login_failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect.")
    token, session = await create_session(db, user, days=settings.session_days)
    audit.record(
        db, user_id=user.id, actor="user", action="logged_in", entity="user", entity_id=user.id
    )
    _set_cookie(response, token, settings)
    return me_out(user, session.csrf_token, settings)


@router.get("/me")
async def me(principal: CurrentUser, settings: AppSettings) -> MeOut:
    return me_out(principal.user, principal.session.csrf_token, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, principal: CurrentUser, db: Db) -> Response:
    token = request.cookies.get(SESSION_COOKIE, "")
    await end_session(db, token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_data(principal: CurrentUser, db: Db) -> Response:
    """Removes the user and, through ON DELETE CASCADE, every row they own."""
    user_id = principal.user.id
    await db.execute(delete(User).where(User.id == user_id))
    logger.info("user_data_deleted", user_id=user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
