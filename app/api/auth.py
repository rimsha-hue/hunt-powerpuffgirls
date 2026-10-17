"""Signup, login, logout, current user and "Delete my data"."""

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete

from app.api.deps import SESSION_COOKIE, AnyPrincipal, AppSettings, CurrentUser, Db
from app.api.ratelimit import rate_limit
from app.api.schemas import LoginIn, MeOut, SignupIn, UserOut
from app.db.models import User
from app.integrations import google_oauth
from app.services import audit, verification
from app.services.auth import (
    authenticate,
    create_session,
    create_user,
    end_session,
    get_user_by_email,
    sign_in_with_google,
    tokens_match,
)
from app.services.signing import sign, unsign

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = structlog.get_logger()

OAUTH_COOKIE = "ph_oauth"
OAUTH_COOKIE_PATH = "/api/auth/google"
OAUTH_TTL_SECONDS = 600


def set_session_cookie(response: Response, token: str, settings: AppSettings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def is_demo_user(user: User, settings: AppSettings) -> bool:
    """The shared account judges and visitors sign in to; it must survive their clicks."""
    demo = (settings.demo_user_email or "").lower()
    return bool(demo) and user.email == demo


def me_out(
    user: User, csrf_token: str, settings: AppSettings, scope_job_id: int | None = None
) -> MeOut:
    return MeOut(
        user=UserOut.model_validate(user),
        csrf_token=csrf_token,
        onboarded=user.profile.resume_version > 0,
        calm_mode=user.profile.calm_mode,
        is_demo=is_demo_user(user, settings),
        scope_job_id=scope_job_id,
    )


def _send_verification(
    request: Request, tasks: BackgroundTasks, db: Db, user: User, settings: AppSettings
) -> None:
    token = verification.issue(db, user, hours=settings.email_verify_hours, now=datetime.now(UTC))
    message = verification.verification_email(
        user, verification.link(settings.base_url, token), settings.email_verify_hours
    )
    tasks.add_task(
        verification.send_quietly, request.app.state.email, message, event="verify_email"
    )


def _alert_sign_in(
    request: Request, tasks: BackgroundTasks, user: User, settings: AppSettings, method: str
) -> None:
    """A sign-in alert to a verified address (an unverified one may not be the user's)."""
    if not settings.login_alerts or not user.email_verified:
        return
    message = verification.login_alert(
        user,
        when=datetime.now(UTC),
        method=method,
        ip=request.client.host if request.client else None,
    )
    tasks.add_task(verification.send_quietly, request.app.state.email, message, event="login_alert")


@router.post(
    "/signup", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit("signup"))]
)
async def signup(
    body: SignupIn,
    request: Request,
    response: Response,
    tasks: BackgroundTasks,
    db: Db,
    settings: AppSettings,
) -> MeOut:
    if await get_user_by_email(db, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists.")
    user = await create_user(db, email=body.email, password=body.password, name=body.name)
    token, session = await create_session(db, user, days=settings.session_days)
    audit.record(
        db, user_id=user.id, actor="user", action="signed_up", entity="user", entity_id=user.id
    )
    _send_verification(request, tasks, db, user, settings)
    set_session_cookie(response, token, settings)
    return me_out(user, session.csrf_token, settings)


@router.get("/verify", include_in_schema=False, dependencies=[Depends(rate_limit("login"))])
async def verify_email(db: Db, t: str = "") -> RedirectResponse:
    """The link from the verification email. It needs no session: it may open in another browser."""
    try:
        await verification.confirm(db, t, now=datetime.now(UTC))
    except verification.VerificationError:
        return RedirectResponse("/profile?verified=failed", status_code=status.HTTP_302_FOUND)
    return RedirectResponse("/profile?verified=1", status_code=status.HTTP_302_FOUND)


@router.post(
    "/verify/resend",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("signup"))],
)
async def resend_verification(
    request: Request,
    tasks: BackgroundTasks,
    principal: CurrentUser,
    db: Db,
    settings: AppSettings,
) -> Response:
    if principal.user.email_verified:
        raise HTTPException(status.HTTP_409_CONFLICT, "Your email is already verified.")
    if not request.app.state.email.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Email isn't set up on this server."
        )
    _send_verification(request, tasks, db, principal.user, settings)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/login", dependencies=[Depends(rate_limit("login"))])
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    tasks: BackgroundTasks,
    db: Db,
    settings: AppSettings,
) -> MeOut:
    user = await authenticate(db, body.email, body.password)
    if user is None:
        logger.info("login_failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect.")
    token, session = await create_session(db, user, days=settings.session_days)
    audit.record(
        db, user_id=user.id, actor="user", action="logged_in", entity="user", entity_id=user.id
    )
    _alert_sign_in(request, tasks, user, settings, "your password")
    set_session_cookie(response, token, settings)
    return me_out(user, session.csrf_token, settings)


@router.get("/google/start", include_in_schema=False, dependencies=[Depends(rate_limit("oauth"))])
async def google_start(settings: AppSettings) -> RedirectResponse:
    if not settings.google_enabled or not settings.google_client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google sign-in is not configured.")
    flow = google_oauth.new_flow()
    response = RedirectResponse(
        google_oauth.authorize_url(settings.google_client_id, settings.google_redirect_uri, flow),
        status_code=status.HTTP_302_FOUND,
    )
    # State, PKCE verifier and nonce ride in a signed, short-lived cookie scoped to this flow.
    response.set_cookie(
        OAUTH_COOKIE,
        sign(
            {"state": flow.state, "verifier": flow.verifier, "nonce": flow.nonce},
            settings.secret_key.get_secret_value(),
            OAUTH_TTL_SECONDS,
            purpose="google-oauth",
        ),
        max_age=OAUTH_TTL_SECONDS,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",  # Lax: the browser sends it on Google's top-level redirect back to us
        path=OAUTH_COOKIE_PATH,
    )
    return response


def _login_redirect(error: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?error={error}", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


@router.get(
    "/google/callback", include_in_schema=False, dependencies=[Depends(rate_limit("oauth"))]
)
async def google_callback(
    request: Request,
    db: Db,
    settings: AppSettings,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if (
        not settings.google_enabled
        or not settings.google_client_id
        or not settings.google_client_secret
    ):
        return _login_redirect("google_unavailable")
    if error:
        return _login_redirect("google_cancelled")

    flow = unsign(
        request.cookies.get(OAUTH_COOKIE, ""),
        settings.secret_key.get_secret_value(),
        purpose="google-oauth",
    )
    if flow is None or not code or not tokens_match(str(flow.get("state", "")), state):
        logger.warning("google_login_rejected", reason="state")
        return _login_redirect("google_failed")

    try:
        id_token = await google_oauth.exchange_code(
            request.app.state.http,
            code=code,
            verifier=str(flow["verifier"]),
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            redirect_uri=settings.google_redirect_uri,
        )
        identity = google_oauth.verify_id_token(
            id_token, client_id=settings.google_client_id, nonce=str(flow["nonce"])
        )
    except google_oauth.GoogleAuthError as exc:
        logger.warning("google_login_rejected", reason=str(exc))
        return _login_redirect("google_failed")

    user, created = await sign_in_with_google(
        db, sub=identity.sub, email=identity.email, name=identity.name
    )
    token, _session = await create_session(db, user, days=settings.session_days)
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="signed_up" if created else "logged_in",
        entity="user",
        entity_id=user.id,
        method="google",
    )
    response = RedirectResponse(
        "/ledger" if user.profile.resume_version > 0 else "/onboarding",
        status_code=status.HTTP_302_FOUND,
    )
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    set_session_cookie(response, token, settings)
    if not created:
        tasks = BackgroundTasks()
        _alert_sign_in(request, tasks, user, settings, "Google")
        response.background = tasks
    return response


@router.get("/me")
async def me(principal: AnyPrincipal, settings: AppSettings) -> MeOut:
    """Also answers for a session opened by an Apply link, so the app can show that one job."""
    session = principal.session
    return me_out(principal.user, session.csrf_token, settings, session.scope_job_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, principal: AnyPrincipal, db: Db) -> Response:
    token = request.cookies.get(SESSION_COOKIE, "")
    await end_session(db, token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_data(principal: CurrentUser, db: Db, settings: AppSettings) -> Response:
    """Removes the user and, through ON DELETE CASCADE, every row they own."""
    if is_demo_user(principal.user, settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The shared demo account can't be deleted.")
    user_id = principal.user.id
    await db.execute(delete(User).where(User.id == user_id))
    logger.info("user_data_deleted", user_id=user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
