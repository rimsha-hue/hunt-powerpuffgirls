"""Growth plans (spec section 7): a skill the ledger can't prove becomes a project, not a claim.

The LLM may write the plan's words; code decides whether they are usable, writes the acceptance
criteria, and checks a linked GitHub repo against them. The skill enters the ledger only when
every check passes.
"""

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.cv import plain
from app.domain.style import style_problems
from app.domain.taxonomy import Taxonomy

PlanStatus = Literal["open", "in_progress", "verified"]
CheckName = Literal["owner", "fresh", "skill", "commits"]

MIN_STEPS = 4
MAX_STEPS = 6
MAX_TITLE = 120
MAX_WHY = 400
MAX_STEP = 200
MIN_HOURS = 2
# A plan is one small project: at most a few weekends.
MAX_HOURS = 30
TEMPLATE_HOURS = 8
# Every plan ends in a GitHub repo, so these never count as extra skills to learn.
PLAN_TOOLS = frozenset({"git", "github"})
# Other skills the user hasn't proven that one plan may also touch (shown to the user), so the
# project stays about the one skill it proves.
MAX_EXTRA_SKILLS = 2

_REPO_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})"
    r"/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class PlanText:
    title: str
    why: str
    steps: tuple[str, ...]
    hours: int


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


@dataclass(frozen=True)
class RepoFacts:
    """What GitHub says about one repository."""

    owner: str
    name: str
    html_url: str
    fork: bool
    created_at: datetime | None
    first_commit_at: datetime | None
    commit_count: int
    languages: tuple[str, ...]
    topics: tuple[str, ...]
    description: str
    readme: str


@dataclass(frozen=True)
class Check:
    name: CheckName
    passed: bool
    detail: str


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment:%B %Y}"  # "6 June 2014", no leading zero


def clean(text: str) -> str:
    return " ".join(plain(text).split())


def normalize_plan(plan: PlanText) -> PlanText:
    return PlanText(
        clean(plan.title), clean(plan.why), tuple(clean(s) for s in plan.steps), plan.hours
    )


def plan_problems(
    plan: PlanText, skill: str, taxonomy: Taxonomy, known: Collection[str] = ()
) -> list[str]:
    """Why a drafted plan can't be shown, empty when it can. `known` are the skills the ledger
    already proves (lowercase): a plan proves one new skill and may touch a couple of others."""
    problems: list[str] = []
    if not MIN_STEPS <= len(plan.steps) <= MAX_STEPS:
        problems.append(f"has {len(plan.steps)} steps, needs {MIN_STEPS} to {MAX_STEPS}")
    if not plan.title or len(plan.title) > MAX_TITLE:
        problems.append(f"the title must be 1 to {MAX_TITLE} characters")
    if not plan.why or len(plan.why) > MAX_WHY:
        problems.append(f"the reason must be 1 to {MAX_WHY} characters")
    if any(not step or len(step) > MAX_STEP for step in plan.steps):
        problems.append(f"every step must be 1 to {MAX_STEP} characters")
    if not MIN_HOURS <= plan.hours <= MAX_HOURS:
        problems.append(f"the estimate must be {MIN_HOURS} to {MAX_HOURS} hours")
    for text in (plan.title, plan.why, *plan.steps):
        problems.extend(style_problems(text))
    found = taxonomy.skills_in(" ".join((plan.title, plan.why, *plan.steps)))
    if skill.lower() not in {s.lower() for s in found}:
        problems.append(f"never names {skill}")
    extra = extra_skills(plan, skill, taxonomy, known)
    if len(extra) > MAX_EXTRA_SKILLS:
        problems.append(
            f"needs {len(extra)} skills the candidate hasn't shown ({', '.join(extra)}); use at"
            f" most {MAX_EXTRA_SKILLS}"
        )
    return list(dict.fromkeys(problems))


def extra_skills(
    plan: PlanText, skill: str, taxonomy: Taxonomy, known: Collection[str]
) -> list[str]:
    """Skills the plan uses besides the one it proves that the ledger doesn't prove yet."""
    found = taxonomy.skills_in(" ".join((plan.title, plan.why, *plan.steps)))
    allowed = {skill.lower(), *PLAN_TOOLS, *(k.lower() for k in known)}
    return sorted(s for s in found if s.lower() not in allowed)


def template_plan(skill: str) -> PlanText:
    """A plan that is always usable, for when no LLM is available or its draft broke the rules."""
    return PlanText(
        title=f"Build and ship a small {skill} project",
        why=(
            f"A public repo that uses {skill} end to end shows you can work with it, and Power"
            " Hunt can check it on GitHub."
        ),
        steps=(
            f"Pick one small problem you can solve with {skill} and describe it in a README.",
            f"Create a new public GitHub repo for it and name {skill} in the README and topics.",
            f"Build the core feature with {skill}, committing as you go.",
            "Add a test or a demo script that shows it working.",
            f"Write in the README how you used {skill} and what you would do next.",
        ),
        hours=TEMPLATE_HOURS,
    )


def acceptance(username: str | None, skill: str, since: datetime, min_commits: int) -> list[str]:
    """The checks Power Hunt runs on a linked repo, in the user's words."""
    owner = (
        f"The repo belongs to github.com/{username} and is not a fork"
        if username
        else "The repo belongs to your GitHub account (add your username in Onboarding)"
    )
    return [
        owner,
        f"It was created, or got its first commit, on or after {_day(since)}",
        f"Its languages, topics, description or README mention {skill}",
        f"It has at least {min_commits} commits",
    ]


def parse_repo_url(url: str) -> RepoRef | None:
    m = _REPO_URL.match(url.strip())
    if m is None or m.group(2) in {".", ".."}:
        return None
    return RepoRef(m.group(1), m.group(2))


def skills_in_repo(facts: RepoFacts, taxonomy: Taxonomy) -> set[str]:
    """Skills named by the repo's languages, topics ("machine-learning"), description or README."""
    found: set[str] = set()
    for term in (*facts.languages, *facts.topics):
        canonical = taxonomy.canonical(term) or taxonomy.canonical(term.replace("-", " "))
        if canonical:
            found.add(canonical.lower())
    text = " ".join((facts.description, facts.readme, *(t.replace("-", " ") for t in facts.topics)))
    found |= {s.lower() for s in taxonomy.skills_in(text)}
    return found


def check_repo(
    facts: RepoFacts,
    *,
    username: str | None,
    skill: str,
    since: datetime,
    taxonomy: Taxonomy,
    min_commits: int,
) -> list[Check]:
    """The four acceptance checks (spec section 7.3), each with a reason the user can act on."""
    owned = bool(username) and facts.owner.lower() == (username or "").lower()
    if not username:
        owner = Check("owner", False, "Add your GitHub username in Onboarding first.")
    elif not owned:
        owner = Check("owner", False, f"It belongs to {facts.owner}, not {username}.")
    elif facts.fork:
        owner = Check("owner", False, "It is a fork; the work must be in a repo you created.")
    else:
        owner = Check("owner", True, f"Owned by {facts.owner}.")

    started = [t for t in (facts.created_at, facts.first_commit_at) if t is not None]
    if any(t >= since for t in started):
        fresh = Check("fresh", True, f"Started on or after {_day(since)}.")
    elif started:
        fresh = Check("fresh", False, f"It was started on {_day(min(started))}, before this plan.")
    else:
        fresh = Check("fresh", False, "GitHub didn't say when it was created.")

    if skill.lower() in skills_in_repo(facts, taxonomy):
        named = Check("skill", True, f"{skill} appears in the repo.")
    else:
        named = Check(
            "skill", False, f"{skill} isn't in its languages, topics, description or README."
        )

    count = facts.commit_count
    commits = Check(
        "commits",
        count >= min_commits,
        f"{count} commit{'s' if count != 1 else ''}"
        + ("." if count >= min_commits else f"; {min_commits} needed."),
    )
    return [owner, fresh, named, commits]


def all_passed(checks: Iterable[Check]) -> bool:
    checks = list(checks)
    return bool(checks) and all(c.passed for c in checks)


def next_status(current: PlanStatus, steps_done: Sequence[bool], repo_linked: bool) -> PlanStatus:
    """Verified is final; otherwise a plan is in progress once a step is done or a repo is linked."""
    if current == "verified":
        return "verified"
    return "in_progress" if any(steps_done) or repo_linked else "open"
