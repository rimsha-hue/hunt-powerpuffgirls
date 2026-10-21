"""Admin console endpoints. Every route needs an admin session (`AdminUser`); every change is
audited. No route returns a user's resume, CV, ledger or email content."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi import status as http
from sqlalchemy.orm import selectinload

from app import __version__
from app.api.auth import send_verification
from app.api.deps import AdminUser, AppSettings, Db
from app.api.health import database_state
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    AdminAuditOut,
    AdminOverviewOut,
    AdminRoleIn,
    AdminSystemOut,
    AdminUserOut,
    AdminUsersOut,
)
from app.db.models import User
from app.domain.access import AdminAction, Role
from app.domain.activity import label
from app.services import admin, audit
from app.services.admin import UserRow

router = APIRouter(prefix="/api/admin", tags=["admin"])

MAX_PAGE = 100


def _out(row: UserRow, settings: AppSettings) -> AdminUserOut:
    user = row.user
    target = admin.account(user, settings.demo_user_email)
    return AdminUserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role="admin" if user.role == "admin" else "user",
        verified=target.verified,
        suspended=target.suspended,
        demo=target.demo,
        google_linked=user.google_linked,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        jobs=row.jobs,
        applications_sent=row.applications_sent,
    )


async def _target(db: Db, user_id: int) -> User:
    user = await db.get(User, user_id, options=[selectinload(User.profile)])
    if user is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No account with that id.")
    return user


async def _act(
    db: Db,
    principal: AdminUser,
    settings: AppSettings,
    user_id: int,
    action: AdminAction,
    new_role: Role | None = None,
) -> User:
    target = await _target(db, user_id)
    try:
        await admin.act(
            db,
            principal.user,
            target,
            action,
            demo_email=settings.demo_user_email,
            now=datetime.now(UTC),
            new_role=new_role,
        )
    except admin.AdminError as exc:
        raise HTTPException(http.HTTP_409_CONFLICT, str(exc)) from exc
    return target


async def _row(db: Db, user: User, settings: AppSettings) -> AdminUserOut:
    rows, _ = await admin.list_users(db, query=user.email, limit=1, offset=0)
    match = next((r for r in rows if r.user.id == user.id), UserRow(user, 0, 0))
    return _out(match, settings)


@router.get("/overview")
async def overview(
    request: Request, principal: AdminUser, db: Db, settings: AppSettings
) -> AdminOverviewOut:
    data = await admin.overview(db, datetime.now(UTC))
    state = request.app.state
    system = AdminSystemOut(
        env=settings.app_env,
        version=__version__,
        database=await database_state(request),
        llm_models=[spec.model for spec in getattr(state.llm, "models", [])],
        embeddings=state.similarity.method,
        scheduler="running" if state.scheduler.running else "not running",
        job_search=bool(settings.rapidapi_key),
        company_research=bool(settings.tavily_api_key),
        email=bool(state.email.configured),
        google_sign_in=settings.google_enabled,
        github_token=bool(settings.github_token),
        rate_limit_storage=settings.rate_limit_storage_uri.split(":", 1)[0],
    )
    return AdminOverviewOut(**data.__dict__, system=system)


@router.get("/users")
async def users(
    principal: AdminUser,
    db: Db,
    settings: AppSettings,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminUsersOut:
    rows, total = await admin.list_users(db, query=q, limit=limit, offset=offset)
    return AdminUsersOut(total=total, users=[_out(r, settings) for r in rows])


MUTATION = [Depends(rate_limit("ingest"))]


@router.post("/users/{user_id}/suspend", dependencies=MUTATION)
async def suspend(
    user_id: int, principal: AdminUser, db: Db, settings: AppSettings
) -> AdminUserOut:
    return await _row(db, await _act(db, principal, settings, user_id, "suspend"), settings)


@router.post("/users/{user_id}/restore", dependencies=MUTATION)
async def restore(
    user_id: int, principal: AdminUser, db: Db, settings: AppSettings
) -> AdminUserOut:
    return await _row(db, await _act(db, principal, settings, user_id, "restore"), settings)


@router.post("/users/{user_id}/sign-out", dependencies=MUTATION)
async def sign_out(
    user_id: int, principal: AdminUser, db: Db, settings: AppSettings
) -> AdminUserOut:
    return await _row(db, await _act(db, principal, settings, user_id, "sign_out"), settings)


@router.put("/users/{user_id}/role", dependencies=MUTATION)
async def set_role(
    user_id: int, body: AdminRoleIn, principal: AdminUser, db: Db, settings: AppSettings
) -> AdminUserOut:
    target = await _act(db, principal, settings, user_id, "set_role", new_role=body.role)
    return await _row(db, target, settings)


@router.post("/users/{user_id}/resend-verification", dependencies=MUTATION)
async def resend_verification(
    user_id: int,
    request: Request,
    tasks: BackgroundTasks,
    principal: AdminUser,
    db: Db,
    settings: AppSettings,
) -> AdminUserOut:
    if not request.app.state.email.configured:
        raise HTTPException(http.HTTP_503_SERVICE_UNAVAILABLE, "Email isn't set up on this server.")
    target = await _act(db, principal, settings, user_id, "resend_verification")
    send_verification(request, tasks, db, target, settings)
    return await _row(db, target, settings)


@router.delete("/users/{user_id}", status_code=http.HTTP_204_NO_CONTENT, dependencies=MUTATION)
async def delete_user(
    user_id: int, principal: AdminUser, db: Db, settings: AppSettings
) -> Response:
    await _act(db, principal, settings, user_id, "delete")
    return Response(status_code=http.HTTP_204_NO_CONTENT)


@router.get("/audit")
async def audit_log(
    principal: AdminUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    action: Annotated[str | None, Query(max_length=60)] = None,
    user_id: int | None = None,
) -> list[AdminAuditOut]:
    rows = await admin.audit_log(db, limit=limit, action=action, user_id=user_id)
    return [
        AdminAuditOut(
            id=r.entry.id,
            created_at=r.entry.created_at,
            actor=r.entry.actor,
            action=r.entry.action,
            label=label(r.entry.action),
            entity=r.entry.entity,
            entity_id=r.entry.entity_id,
            user_id=r.entry.user_id,
            email=r.email,
        )
        for r in rows
    ]


@router.post(
    "/scout",
    status_code=http.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("scout"))],
)
async def run_scout_for_everyone(
    request: Request, tasks: BackgroundTasks, principal: AdminUser, db: Db
) -> Response:
    """Starts every user's Scout in the background (it spends the job APIs' quotas)."""
    audit.record(
        db,
        user_id=principal.user.id,
        actor="admin",
        action="admin_scout_all",
        entity="scout",
    )
    tasks.add_task(request.app.state.scout_runner.run_all)
    return Response(status_code=http.HTTP_202_ACCEPTED)
