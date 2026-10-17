"""Coach (spec section 7): a skill gap becomes a growth plan, and a linked GitHub repo that passes
every acceptance check turns the gap into Verified evidence.

The LLM writes the plan's words (checked by `domain/coach.py`, with a template when it can't);
code writes the acceptance criteria and runs them against what GitHub reports.
"""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, Claim, ClaimBlocklist, Evidence, Job, SkillPlan, User
from app.domain.coach import (
    MAX_IDEAS,
    MAX_MILESTONES,
    MAX_SCOPE,
    MAX_STEP,
    MIN_IDEAS,
    MIN_MILESTONES,
    PlanText,
    ProjectIdea,
    all_passed,
    check_repo,
    idea_problems,
    next_status,
    normalize_idea,
    normalize_plan,
    parse_repo_url,
    plan_problems,
    template_ideas,
    template_plan,
)
from app.domain.locations import LocationIndex
from app.domain.taxonomy import Taxonomy
from app.integrations.github import GitHubClient, GitHubError
from app.integrations.llm import LLMError, StructuredLLM
from app.services import audit
from app.services.scout import rescore_user_jobs
from app.services.tailor import MAX_JOB_TEXT

logger = structlog.get_logger()

# Skills the user already proves, given to the model so the project can build on them.
MAX_KNOWN_SKILLS = 40

COACH_SYSTEM = (
    "You plan one small portfolio project that proves a skill to an employer. The project uses "
    "the skill for real and ends in a public GitHub repository. Keep it small: 6 to 30 hours. "
    "Build mainly on the skill being proven and skills the candidate already has; use at most "
    "two other new tools. Write plainly, with no em or en dashes and no hype words. Give "
    "a specific title, one or two sentences on why the project proves the skill, 4 to 6 concrete "
    f"steps in order (each one sentence, at most {MAX_STEP} characters), and an honest estimate in "
    "hours. Name the skill in the title or the steps."
)
COACH_TASK = (
    "Plan one project that proves the skill named in the data. Where it helps, build on skills "
    "the candidate already has."
)


class PlanDraft(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    why: str = Field(min_length=1, max_length=600)
    steps: list[str] = Field(min_length=1, max_length=8)
    hours: int = Field(ge=1, le=200)


IDEAS_SYSTEM = (
    "You suggest concrete portfolio projects that prove one skill to an employer. Each idea is a "
    "small real product with a clear user, not a tutorial exercise, and each idea is different "
    "in kind from the others (for example a tool, a service, an analysis). The skill is used for "
    "the project's core job. Keep each to 6 to 30 hours and build on the candidate's skills. "
    "Write plainly, with no em or en dashes and no hype words."
)
IDEAS_TASK = (
    f"Suggest {MIN_IDEAS} to {MAX_IDEAS} project ideas for the skill in the data. For each: a "
    "specific title; a pitch (what it is, one or two sentences); the problem (who has it); the "
    f"first version's scope (1 to {MAX_SCOPE} short items); the stack (tools, including the "
    f"skill); {MIN_MILESTONES} to {MAX_MILESTONES} milestones in order; how the finished public "
    "repo proves the skill; and an honest estimate in hours."
)


class IdeaDraft(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    pitch: str = Field(min_length=1, max_length=600)
    problem: str = Field(min_length=1, max_length=600)
    scope: list[str] = Field(min_length=1, max_length=8)
    stack: list[str] = Field(min_length=1, max_length=10)
    milestones: list[str] = Field(min_length=1, max_length=8)
    proof: str = Field(min_length=1, max_length=600)
    hours: int = Field(ge=1, le=200)


class IdeasDraft(BaseModel):
    ideas: list[IdeaDraft] = Field(min_length=1, max_length=6)


class CoachError(Exception):
    """Safe to show to the user."""


def _aware(moment: datetime) -> datetime:
    # SQLite hands back naive datetimes; every stored time is UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def plan_day(plan: SkillPlan) -> datetime:
    """The start of the day the plan was made; a repo started that day counts."""
    return _aware(plan.created_at).replace(hour=0, minute=0, second=0, microsecond=0)


async def _known_skills(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.execute(
        select(Evidence.claim_text).where(Evidence.user_id == user_id, Evidence.type == "skill")
    )
    return set(rows.scalars())


async def _draft(
    skill: str, job: Job | None, known: set[str], llm: StructuredLLM, taxonomy: Taxonomy
) -> tuple[PlanText, str]:
    """(plan, written_by). One rewrite when the draft breaks a rule, then the template."""
    if not llm.configured:
        return template_plan(skill), "template"
    data = {"skill": skill, "candidate_skills": ", ".join(sorted(known)[:MAX_KNOWN_SKILLS])}
    if job is not None:
        data["job"] = f"{job.title} at {job.company}\n\n{job.description[:MAX_JOB_TEXT]}"
    # Every problem seen so far, so fixing one doesn't bring back another.
    seen: list[str] = []
    for _ in range(2):
        task = COACH_TASK
        if seen:
            task += f" Earlier drafts broke these rules: {'; '.join(seen)}. Avoid all of them."
        try:
            draft = await llm.generate(schema=PlanDraft, system=COACH_SYSTEM, task=task, data=data)
        except LLMError as exc:
            logger.warning("coach_llm_failed", error_type=type(exc).__name__)
            break
        plan = normalize_plan(PlanText(draft.title, draft.why, tuple(draft.steps), draft.hours))
        problems = plan_problems(plan, skill, taxonomy, {k.lower() for k in known})
        if not problems:
            return plan, "llm"
        logger.info("coach_draft_rejected", problems=problems)
        seen.extend(p for p in problems if p not in seen)
    return template_plan(skill), "template"


async def suggest_ideas(
    db: AsyncSession, plan: SkillPlan, *, llm: StructuredLLM, taxonomy: Taxonomy, now: datetime
) -> list[ProjectIdea]:
    """2 to 3 project ideas for a plan's skill, stored on the plan. Ideas that break a rule are
    dropped; if too few remain after one rewrite, template ideas fill the gap."""
    known = await _known_skills(db, plan.user_id)
    data = {"skill": plan.skill, "candidate_skills": ", ".join(sorted(known)[:MAX_KNOWN_SKILLS])}
    kept: list[ProjectIdea] = []
    seen: list[str] = []
    for _ in range(2 if llm.configured else 0):
        task = IDEAS_TASK + (
            f" Earlier ideas broke these rules: {'; '.join(seen)}." if seen else ""
        )
        try:
            draft = await llm.generate(schema=IdeasDraft, system=IDEAS_SYSTEM, task=task, data=data)
        except LLMError as exc:
            logger.warning("coach_ideas_failed", error_type=type(exc).__name__)
            break
        for item in draft.ideas:
            idea = normalize_idea(
                ProjectIdea(
                    title=item.title,
                    pitch=item.pitch,
                    problem=item.problem,
                    scope=tuple(item.scope),
                    stack=tuple(item.stack),
                    milestones=tuple(item.milestones),
                    proof=item.proof,
                    hours=item.hours,
                )
            )
            problems = idea_problems(idea, plan.skill, taxonomy)
            if problems:
                seen.extend(p for p in problems if p not in seen)
            elif idea.title.lower() not in {k.title.lower() for k in kept}:
                kept.append(idea)
        if len(kept) >= MIN_IDEAS:
            break
    # Templates only fill the gap; with no usable LLM idea the user gets every template.
    ideas = kept[:MAX_IDEAS] if kept else []
    for template in template_ideas(plan.skill):
        if kept and len(ideas) >= MIN_IDEAS:
            break
        ideas.append(template)
    plan.ideas_json = [idea.to_json() for idea in ideas[:MAX_IDEAS]]
    plan.updated_at = now
    audit.record(
        db,
        user_id=plan.user_id,
        actor="coach",
        action="ideas_suggested",
        entity="plan",
        entity_id=plan.id,
        from_llm=len(kept),
    )
    return [ProjectIdea.from_json(i) for i in plan.ideas_json]


async def create_plan(
    db: AsyncSession,
    user: User,
    term: str,
    *,
    job: Job | None,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    now: datetime,
) -> SkillPlan:
    """A plan for one skill. Asking again for the same skill returns the existing plan."""
    skill = taxonomy.canonical(term)
    if skill is None:
        raise CoachError("Power Hunt doesn't know that skill, so it can't plan for it.")
    key = skill.lower()
    existing = (
        await db.execute(
            select(SkillPlan).where(SkillPlan.user_id == user.id, SkillPlan.skill_key == key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    if await _proven(db, user.id, key):
        raise CoachError(f"Your ledger already proves {skill}.")

    known = await _known_skills(db, user.id)
    text, written_by = await _draft(skill, job, known, llm, taxonomy)
    plan = SkillPlan(
        user_id=user.id,
        job_id=job.id if job is not None else None,
        skill=skill,
        skill_key=key,
        title=text.title,
        why=text.why,
        steps_json=[{"text": step, "done": False} for step in text.steps],
        hours=text.hours,
        status="open",
        written_by=written_by,
        checks_json=[],
        created_at=now,
        updated_at=now,
    )
    db.add(plan)
    await db.flush()
    audit.record(
        db,
        user_id=user.id,
        actor="coach",
        action="plan_created",
        entity="skill_plan",
        entity_id=plan.id,
        written_by=written_by,
    )
    return plan


async def _proven(db: AsyncSession, user_id: int, key: str) -> bool:
    row = await db.execute(
        select(Evidence.id).where(
            Evidence.user_id == user_id,
            Evidence.type == "skill",
            Evidence.normalized_value == key,
        )
    )
    return row.first() is not None


def set_step(plan: SkillPlan, index: int, done: bool, now: datetime) -> None:
    steps: list[dict[str, Any]] = [dict(s) for s in plan.steps_json]
    if not 0 <= index < len(steps):
        raise CoachError("That step doesn't exist.")
    steps[index]["done"] = done
    plan.steps_json = steps  # a new list, so the JSON column is saved
    plan.status = next_status(
        plan.status,  # type: ignore[arg-type]
        [bool(s.get("done")) for s in steps],
        plan.repo_url is not None,
    )
    plan.updated_at = now


async def verify_repo(
    db: AsyncSession,
    user: User,
    plan: SkillPlan,
    url: str,
    *,
    github: GitHubClient,
    taxonomy: Taxonomy,
    locations: LocationIndex,
    min_commits: int,
    readme_max_chars: int,
    now: datetime,
) -> bool:
    """Checks the repo against the plan's acceptance criteria. True when the skill is now proven."""
    if plan.status == "verified":
        raise CoachError("This plan is already verified.")
    ref = parse_repo_url(url)
    if ref is None:
        raise CoachError("Paste a GitHub repository link, like https://github.com/you/project.")
    try:
        facts = await github.repo_facts(ref, readme_max_chars=readme_max_chars)
    except GitHubError as exc:
        raise CoachError(str(exc)) from exc
    checks = check_repo(
        facts,
        username=user.github_username,
        skill=plan.skill,
        since=plan_day(plan),
        taxonomy=taxonomy,
        min_commits=min_commits,
    )
    passed = all_passed(checks)
    plan.repo_url = facts.html_url[:300]
    plan.checks_json = [asdict(c) for c in checks]
    plan.updated_at = now
    if passed:
        plan.status = "verified"
        plan.verified_at = now
        await _prove(db, user, plan, now)
        await rescore_user_jobs(db, user, locations)
    else:
        plan.status = next_status(
            plan.status,  # type: ignore[arg-type]
            [bool(s.get("done")) for s in plan.steps_json],
            repo_linked=True,
        )
    audit.record(
        db,
        user_id=user.id,
        actor="coach",
        action="plan_verified" if passed else "plan_check_failed",
        entity="skill_plan",
        entity_id=plan.id,
        failed=[c.name for c in checks if not c.passed],
    )
    return passed


async def _prove(db: AsyncSession, user: User, plan: SkillPlan, now: datetime) -> None:
    """The skill joins the ledger from the repo. Open Needs-you claims for it in drafts are now
    Verified, and an earlier "mark false" for it gives way to the new proof."""
    url = plan.repo_url or ""
    cleared = await db.execute(
        delete(ClaimBlocklist).where(
            ClaimBlocklist.user_id == user.id, ClaimBlocklist.normalized_value == plan.skill_key
        )
    )
    evidence = (
        await db.execute(
            select(Evidence).where(
                Evidence.user_id == user.id,
                Evidence.type == "skill",
                Evidence.normalized_value == plan.skill_key,
                Evidence.source == "github",
            )
        )
    ).scalar_one_or_none()
    if evidence is None:
        evidence = Evidence(
            user_id=user.id,
            claim_text=plan.skill,
            type="skill",
            normalized_value=plan.skill_key,
            source="github",
            source_ref=url,
            confirmed_by="system",
            confidence=1.0,
            created_at=now,
        )
        db.add(evidence)
        await db.flush()
    claims = await db.execute(
        select(Claim)
        .join(Application, Application.id == Claim.application_id)
        .where(
            Claim.user_id == user.id,
            Claim.type == "skill",
            Claim.normalized_value == plan.skill_key,
            Claim.bucket == "needs_you",
            Application.status == "draft",
        )
    )
    updated = 0
    for claim in claims.scalars():
        claim.bucket = "verified"
        claim.evidence_id = evidence.id
        updated += 1
    audit.record(
        db,
        user_id=user.id,
        actor="coach",
        action="skill_proven_by_repo",
        entity="evidence",
        entity_id=evidence.id,
        plan_id=plan.id,
        claims_verified=updated,
        blocklist_cleared=bool(getattr(cleared, "rowcount", 0)),
    )
