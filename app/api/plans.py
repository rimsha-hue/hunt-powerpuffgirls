"""Coach endpoints: growth plans for skill gaps and GitHub verification (spec section 7)."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.api.applications import job_for
from app.api.deps import LLM, AppSettings, CurrentUser, Db, GitHub, Locations, Principal, Tax
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    PlanCheckOut,
    PlanCreateIn,
    PlanOut,
    PlanStepIn,
    PlanStepOut,
    PlanVerifyIn,
    PlanVerifyOut,
)
from app.db.models import Evidence, Job, SkillPlan
from app.domain.coach import PlanText, acceptance, extra_skills
from app.domain.taxonomy import Taxonomy
from app.services import audit
from app.services.coach import CoachError, create_plan, plan_day, set_step, verify_repo

router = APIRouter(prefix="/api/plans", tags=["plans"])

STATUS_ORDER = {"in_progress": 0, "open": 1, "verified": 2}


async def _plan_for(db: Db, principal: Principal, plan_id: int) -> SkillPlan:
    plan = await db.get(SkillPlan, plan_id)
    if plan is None or plan.user_id != principal.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    return plan


async def proven_skills(db: Db, user_id: int) -> set[str]:
    rows = await db.execute(
        select(Evidence.normalized_value).where(
            Evidence.user_id == user_id, Evidence.type == "skill"
        )
    )
    return set(rows.scalars())


async def plan_out(
    db: Db,
    plan: SkillPlan,
    principal: Principal,
    settings: AppSettings,
    taxonomy: Taxonomy,
    known: set[str] | None = None,
) -> PlanOut:
    job = await db.get(Job, plan.job_id) if plan.job_id is not None else None
    if known is None:
        known = await proven_skills(db, principal.user.id)
    text = PlanText(
        plan.title, plan.why, tuple(str(s["text"]) for s in plan.steps_json), plan.hours
    )
    return PlanOut(
        id=plan.id,
        skill=plan.skill,
        title=plan.title,
        why=plan.why,
        steps=[PlanStepOut(text=str(s["text"]), done=bool(s["done"])) for s in plan.steps_json],
        hours=plan.hours,
        status=plan.status,
        written_by=plan.written_by,
        acceptance=acceptance(
            principal.user.github_username, plan.skill, plan_day(plan), settings.coach_min_commits
        ),
        also_new=[]
        if plan.status == "verified"
        else extra_skills(text, plan.skill, taxonomy, known),
        repo_url=plan.repo_url,
        checks=[PlanCheckOut(**c) for c in plan.checks_json],
        job_id=plan.job_id,
        job_title=f"{job.title} at {job.company}" if job is not None else None,
        created_at=plan.created_at,
        verified_at=plan.verified_at,
    )


@router.get("")
async def list_plans(
    principal: CurrentUser, db: Db, settings: AppSettings, taxonomy: Tax
) -> list[PlanOut]:
    rows = await db.execute(
        select(SkillPlan)
        .where(SkillPlan.user_id == principal.user.id)
        .order_by(SkillPlan.created_at.desc(), SkillPlan.id.desc())
    )
    plans = sorted(rows.scalars(), key=lambda p: STATUS_ORDER.get(p.status, 3))
    known = await proven_skills(db, principal.user.id)
    return [await plan_out(db, p, principal, settings, taxonomy, known) for p in plans]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit("ingest"))])
async def new_plan(
    body: PlanCreateIn,
    principal: CurrentUser,
    db: Db,
    llm: LLM,
    taxonomy: Tax,
    settings: AppSettings,
) -> PlanOut:
    if taxonomy.canonical(body.skill) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [
                {
                    "type": "value_error",
                    "loc": ["body", "skill"],
                    "msg": "Power Hunt doesn't know that skill, so it can't plan for it.",
                }
            ],
        )
    job = await job_for(db, principal, body.job_id) if body.job_id is not None else None
    try:
        plan = await create_plan(
            db,
            principal.user,
            body.skill,
            job=job,
            llm=llm,
            taxonomy=taxonomy,
            now=datetime.now(UTC),
        )
    except CoachError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await plan_out(db, plan, principal, settings, taxonomy)


@router.put("/{plan_id}/steps/{index}")
async def update_step(
    plan_id: int,
    index: int,
    body: PlanStepIn,
    principal: CurrentUser,
    db: Db,
    settings: AppSettings,
    taxonomy: Tax,
) -> PlanOut:
    plan = await _plan_for(db, principal, plan_id)
    try:
        set_step(plan, index, body.done, datetime.now(UTC))
    except CoachError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return await plan_out(db, plan, principal, settings, taxonomy)


@router.post("/{plan_id}/verify", dependencies=[Depends(rate_limit("ingest"))])
async def verify_plan(
    plan_id: int,
    body: PlanVerifyIn,
    principal: CurrentUser,
    db: Db,
    github: GitHub,
    taxonomy: Tax,
    locations: Locations,
    settings: AppSettings,
) -> PlanVerifyOut:
    plan = await _plan_for(db, principal, plan_id)
    try:
        verified = await verify_repo(
            db,
            principal.user,
            plan,
            body.repo_url,
            github=github,
            taxonomy=taxonomy,
            locations=locations,
            min_commits=settings.coach_min_commits,
            readme_max_chars=settings.coach_readme_max_chars,
            now=datetime.now(UTC),
        )
    except CoachError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PlanVerifyOut(
        plan=await plan_out(db, plan, principal, settings, taxonomy), verified=verified
    )


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(plan_id: int, principal: CurrentUser, db: Db) -> Response:
    plan = await _plan_for(db, principal, plan_id)
    await db.delete(plan)
    audit.record(
        db,
        user_id=principal.user.id,
        actor="user",
        action="plan_deleted",
        entity="skill_plan",
        entity_id=plan_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
