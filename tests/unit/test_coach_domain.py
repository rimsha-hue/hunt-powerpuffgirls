from datetime import UTC, datetime

import pytest

from app.domain.activity import GATES, label
from app.domain.coach import (
    PlanText,
    ProjectIdea,
    RepoFacts,
    acceptance,
    idea_problems,
    proposal_markdown,
    template_ideas,
    all_passed,
    check_repo,
    extra_skills,
    next_status,
    normalize_plan,
    parse_repo_url,
    plan_problems,
    template_plan,
)
from app.domain.taxonomy import default_taxonomy

TAX = default_taxonomy()
SINCE = datetime(2026, 10, 17, tzinfo=UTC)
KNOWN = {"fastapi", "docker", "python"}

GOOD = PlanText(
    title="Deploy a FastAPI service on Kubernetes",
    why="Running your own service on a cluster shows you can ship with Kubernetes.",
    steps=(
        "Containerise a small FastAPI app with Docker.",
        "Write Kubernetes manifests for a deployment and a service.",
        "Run it on a local kind cluster and add a readiness probe.",
        "Document the setup and commands in the README.",
    ),
    hours=10,
)


def _repo(**changes: object) -> RepoFacts:
    values: dict[str, object] = {
        "owner": "ayesha-dev",
        "name": "k8s-demo",
        "html_url": "https://github.com/ayesha-dev/k8s-demo",
        "fork": False,
        "created_at": datetime(2026, 10, 18, tzinfo=UTC),
        "first_commit_at": datetime(2026, 10, 18, tzinfo=UTC),
        "commit_count": 5,
        "languages": ("Python", "Dockerfile"),
        "topics": ("kubernetes", "fastapi"),
        "description": "A FastAPI service on a cluster",
        "readme": "",
    }
    values.update(changes)
    return RepoFacts(**values)  # type: ignore[arg-type]


def _checks(facts: RepoFacts, username: str | None = "ayesha-dev") -> dict[str, bool]:
    checks = check_repo(
        facts, username=username, skill="Kubernetes", since=SINCE, taxonomy=TAX, min_commits=3
    )
    return {c.name: c.passed for c in checks}


class TestIdeas:
    @pytest.mark.parametrize("skill", ["Kubernetes", "PyTorch", "SQL"])
    def test_templates_always_pass_the_rules(self, skill: str) -> None:
        ideas = template_ideas(skill)
        assert len(ideas) == 3 and len({i.title for i in ideas}) == 3
        assert all(idea_problems(i, skill, TAX) == [] for i in ideas)

    def test_an_idea_must_use_the_skill_and_be_sized(self) -> None:
        idea = template_ideas("Kubernetes")[0]
        assert "never uses SQL" in idea_problems(idea, "SQL", TAX)
        short = ProjectIdea(**{**idea.to_json(), "milestones": ("Ship it.",)})  # type: ignore[arg-type]
        assert any("milestones" in p for p in idea_problems(short, "Kubernetes", TAX))

    def test_round_trip_and_proposal(self) -> None:
        idea = template_ideas("Kubernetes")[1]
        assert ProjectIdea.from_json(idea.to_json()) == idea
        page = proposal_markdown(idea, "Kubernetes")
        assert page.startswith(f"# {idea.title}") and "## How it proves Kubernetes" in page
        assert all(m in page for m in idea.milestones)


class TestPlanText:
    def test_a_good_plan_has_no_problems(self) -> None:
        assert plan_problems(GOOD, "Kubernetes", TAX, KNOWN) == []

    def test_the_template_always_passes(self) -> None:
        for skill in ("Kubernetes", "Apache Kafka", "Terraform", "Go"):
            assert plan_problems(template_plan(skill), skill, TAX) == [], skill

    @pytest.mark.parametrize(
        ("change", "problem"),
        [
            ({"steps": GOOD.steps[:3]}, "has 3 steps"),
            ({"hours": 500}, "estimate"),
            ({"why": "A passionate project about Kubernetes."}, 'uses "passionate"'),
            ({"title": "Ship it — on Kubernetes"}, "dash"),
            (
                {
                    "title": "Build a web app",
                    "why": "It shows you can build things.",
                    "steps": ("Plan it.", "Build it.", "Test it.", "Ship it."),
                },
                "never names Kubernetes",
            ),
        ],
    )
    def test_problems(self, change: dict[str, object], problem: str) -> None:
        plan = PlanText(**{**GOOD.__dict__, **change})  # type: ignore[arg-type]
        assert any(problem in p for p in plan_problems(plan, "Kubernetes", TAX, KNOWN))

    def test_a_plan_may_touch_two_other_new_skills_not_more(self) -> None:
        def with_step(step: str) -> PlanText:
            return PlanText(**{**GOOD.__dict__, "steps": (*GOOD.steps[:3], step)})

        two = with_step("Provision it with Terraform and add Prometheus metrics.")
        assert plan_problems(two, "Kubernetes", TAX, KNOWN) == []
        assert extra_skills(two, "Kubernetes", TAX, KNOWN) == ["Prometheus", "Terraform"]
        three = with_step("Provision it with Terraform, Prometheus and Apache Kafka.")
        [problem] = plan_problems(three, "Kubernetes", TAX, KNOWN)
        assert "needs 3 skills" in problem and "Apache Kafka" in problem
        # GitHub and Git are part of every plan, never an extra skill.
        assert plan_problems(template_plan("Kubernetes"), "Kubernetes", TAX, set()) == []

    def test_model_typography_is_made_plain(self) -> None:
        plan = normalize_plan(PlanText("Run Kubernetes  jobs", "x", ("a‑b",), 3))
        assert plan.title == "Run Kubernetes jobs" and plan.steps == ("a-b",)

    def test_acceptance_states_every_check(self) -> None:
        criteria = acceptance("ayesha-dev", "Kubernetes", SINCE, 3)
        assert len(criteria) == 4
        assert "github.com/ayesha-dev" in criteria[0]
        assert "17 October 2026" in criteria[1]
        assert "Kubernetes" in criteria[2] and "3 commits" in criteria[3]
        assert "Onboarding" in acceptance(None, "Kubernetes", SINCE, 3)[0]


class TestRepoUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/ayesha-dev/k8s-demo",
            "https://github.com/ayesha-dev/k8s-demo/",
            "https://github.com/ayesha-dev/k8s-demo.git",
            "http://www.github.com/ayesha-dev/k8s-demo",
        ],
    )
    def test_accepted(self, url: str) -> None:
        ref = parse_repo_url(url)
        assert ref is not None and (ref.owner, ref.name) == ("ayesha-dev", "k8s-demo")

    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.com/ayesha-dev/k8s-demo",
            "https://github.com/ayesha-dev",
            "https://github.com/ayesha-dev/k8s-demo/tree/main",
            "https://github.com.evil.example/ayesha-dev/k8s-demo",
            "https://github.com/-bad-/repo",
            "https://github.com/ayesha-dev/..",
            "javascript:alert(1)",
        ],
    )
    def test_refused(self, url: str) -> None:
        assert parse_repo_url(url) is None


class TestRepoChecks:
    def test_a_qualifying_repo_passes_every_check(self) -> None:
        checks = check_repo(
            _repo(),
            username="Ayesha-Dev",
            skill="Kubernetes",
            since=SINCE,
            taxonomy=TAX,
            min_commits=3,
        )
        assert all_passed(checks)

    def test_someone_elses_repo_or_a_fork_fails_ownership(self) -> None:
        assert not _checks(_repo(owner="octocat"))["owner"]
        assert not _checks(_repo(fork=True))["owner"]
        assert not _checks(_repo(), username=None)["owner"]

    def test_a_repo_older_than_the_plan_fails(self) -> None:
        old = datetime(2025, 1, 1, tzinfo=UTC)
        assert not _checks(_repo(created_at=old, first_commit_at=old))["fresh"]
        # An old empty repo whose first commit came after the plan counts.
        assert _checks(_repo(created_at=old))["fresh"]

    def test_the_skill_must_appear_in_the_repo(self) -> None:
        assert not _checks(_repo(topics=(), description="A web app"))["skill"]
        assert _checks(_repo(topics=(), description="", readme="Deployed with k8s."))["skill"]
        assert _checks(_repo(topics=("kubernetes",), description=""))["skill"]

    def test_too_few_commits_fails(self) -> None:
        assert not _checks(_repo(commit_count=2))["commits"]
        assert _checks(_repo(commit_count=3))["commits"]

    def test_status(self) -> None:
        assert next_status("open", [False, False], False) == "open"
        assert next_status("open", [True, False], False) == "in_progress"
        assert next_status("open", [False], True) == "in_progress"
        assert next_status("verified", [False], False) == "verified"


class TestActivityLabels:
    def test_known_and_unknown_actions(self) -> None:
        assert label("gate_submit").startswith("You submitted")
        assert label("something_new") == "Something new"
        assert GATES["gate_choose"] == 1 and GATES["gate_submit"] == 4
