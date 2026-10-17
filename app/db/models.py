"""ORM models. Every user-owned row cascades on user delete ("Delete my data")."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that is always timezone-aware UTC when read back.

    Postgres keeps the zone; SQLite (tests, the local fallback) drops it, and comparing a naive
    value with an aware one raises. Every timestamp column uses this type, so code never has to
    guess which kind it was given."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware.")
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


def _user_fk() -> Mapped[int]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("google_sub", name="uq_users_google_sub"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    # Empty for accounts created through Google: no password login is possible for them.
    password_hash: Mapped[str] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(255))
    github_username: Mapped[str | None] = mapped_column(String(100))
    portfolio_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    @property
    def google_linked(self) -> bool:
        return self.google_sub is not None

    @property
    def has_password(self) -> bool:
        """False for accounts made with Google sign-in, which have no usable password."""
        return bool(self.password_hash)

    profile: Mapped["Profile"] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class Profile(Base):
    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    phone: Mapped[str | None] = mapped_column(String(20))  # E.164
    # Places are stored as location keys ("city:1176615", "country:PK", "remote"), never free text.
    location: Mapped[str | None] = mapped_column(String(40))
    linkedin: Mapped[str | None] = mapped_column(String(200))  # canonical profile URL
    work_authorization: Mapped[str | None] = mapped_column(String(30))  # WorkAuthorization value
    notice_period: Mapped[str | None] = mapped_column(String(20))  # NoticePeriod value
    salary_amount: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))  # ISO 4217
    salary_period: Mapped[str | None] = mapped_column(String(10))  # "month" or "year"
    target_roles: Mapped[list[str]] = mapped_column(default=list)
    target_locations: Mapped[list[str]] = mapped_column(default=list)  # location keys
    match_threshold: Mapped[int] = mapped_column(Integer, default=60)
    calm_mode: Mapped[bool] = mapped_column(default=False)
    # Answers to form questions the profile has no field for ("How did you hear about us?"),
    # keyed by the question's folded label, so the user is asked once (spec Flow D).
    saved_answers: Mapped[dict[str, Any]] = mapped_column(default=dict)
    resume_version: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="profile")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    # A session minted from a signed Apply link is limited to one job (Phase 2).
    scope_job_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ResumeLine(Base):
    __tablename__ = "resume_lines"
    __table_args__ = (Index("ix_resume_lines_user_version", "user_id", "resume_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    resume_version: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(40))
    line_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # A bullet describes the entry above it; the Tailor may rewrite and reorder only bullets.
    bulleted: Mapped[bool] = mapped_column(default=False)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        # One row per fact, source and entry: the same role at two companies is two facts. An
        # expression, because a unique constraint treats every NULL entry as different.
        Index(
            "uq_evidence_fact_source_entry",
            "user_id",
            "type",
            "normalized_value",
            "source",
            text("coalesce(context, '')"),
            unique=True,
        ),
        Index("ix_evidence_user_type_value", "user_id", "type", "normalized_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    claim_text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(20))
    normalized_value: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str] = mapped_column(String(500))
    # The entry a fact belongs to, e.g. the project a metric was achieved in.
    context: Mapped[str | None] = mapped_column(String(200))
    confirmed_by: Mapped[str] = mapped_column(String(10), default="system")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ClaimBlocklist(Base):
    __tablename__ = "claim_blocklist"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    normalized_value: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Job(Base):
    """A posting found by the Scout for one user, with its frozen ATS keywords and scores."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_jobs_source_id"),
        UniqueConstraint("user_id", "dedupe_hash", name="uq_jobs_dedupe_hash"),
        Index("ix_jobs_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(200))
    # sha256 of normalised company + title + location, so one posting on two boards is one job.
    dedupe_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(200), default="")
    remote: Mapped[bool] = mapped_column(default=False)
    url: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # When the source says applications close, and when the posting's own site last said it was
    # still open (services/liveness.py).
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    open_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    apply_method: Mapped[str] = mapped_column(String(10))  # email | form | unknown
    apply_email: Mapped[str | None] = mapped_column(String(320))
    apply_url: Mapped[str | None] = mapped_column(String(1000))
    required_attachments: Mapped[list[str]] = mapped_column(default=list)
    # Frozen when first scored, so the "before" and "after" ATS scores use the same list.
    ats_keywords_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    ats_before: Mapped[int] = mapped_column(Integer, default=0)
    ats_detail_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    matched_skills: Mapped[list[str]] = mapped_column(default=list)
    missing_skills: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(20), default="new")
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Set when the job went out in a digest, so each job is announced once.
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ApplyToken(Base):
    """A signed Apply link: only the hash is stored; single use, expiring, bound to user and job."""

    __tablename__ = "apply_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    kind: Mapped[str] = mapped_column(String(20))  # "digest"
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    job_ids: Mapped[list[int]] = mapped_column(JSON, default=list)


class Application(Base):
    """One application per job: the tailored CV and, from Phase 4, the email or form answers."""

    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id", name="uq_applications_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(10))
    # draft -> cv_approved (Gate 2 passed) -> sent (email) or submitted (form, by the user).
    status: Mapped[str] = mapped_column(String(20), default="draft")
    tailored_cv_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    tailor_note: Mapped[str | None] = mapped_column(String(300))
    resume_version: Mapped[int] = mapped_column(Integer, default=0)
    ats_after: Mapped[int] = mapped_column(Integer, default=0)
    ats_after_detail_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Claims sent to the user when the CV was first verified; should fall job after job.
    questions_asked: Mapped[int] = mapped_column(Integer, default=0)
    cv_approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cover_letter: Mapped[str | None] = mapped_column(Text)
    # Sentence blocks of the email body and cover letter, verified like the CV (Phase 4).
    email_blocks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    cover_letter_blocks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    recipient: Mapped[str | None] = mapped_column(String(320))
    # Where the form's questions came from: greenhouse, lever, page or pasted (Phase 5).
    form_source: Mapped[str | None] = mapped_column(String(20))
    form_url: Mapped[str | None] = mapped_column(String(1000))
    compose_note: Mapped[str | None] = mapped_column(String(500))
    email_subject: Mapped[str | None] = mapped_column(String(300))
    email_body: Mapped[str | None] = mapped_column(Text)
    attachments_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    answers_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    message_id: Mapped[str | None] = mapped_column(String(300))
    follow_up_due: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Claim(Base):
    """One atomic claim in generated text, with where it sits and how it was routed."""

    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_application_bucket", "application_id", "bucket"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = _user_fk()
    # Which generated text the claim sits in: cv, email or cover_letter.
    document: Mapped[str] = mapped_column(String(12), default="cv")
    block_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(500))
    type: Mapped[str] = mapped_column(String(20))
    normalized_value: Mapped[str] = mapped_column(String(500), default="")
    span_start: Mapped[int] = mapped_column(Integer)
    span_end: Mapped[int] = mapped_column(Integer)
    # verified | inferred | needs_you | blocked | removed
    bucket: Mapped[str] = mapped_column(String(12))
    origin: Mapped[str] = mapped_column(String(8))  # "code" or "llm"
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("evidence.id", ondelete="SET NULL"))
    cited_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_lines.id", ondelete="SET NULL")
    )
    similarity: Mapped[float | None] = mapped_column(Float)
    # The role or degree an organisation claim is stated with ("ML Engineer" in "ML Engineer at
    # Acme"). The pair is the claim: confirming it records both under one ledger entry.
    pair_type: Mapped[str | None] = mapped_column(String(20))
    pair_value: Mapped[str | None] = mapped_column(String(500))
    pair_text: Mapped[str | None] = mapped_column(String(500))
    # confirmed | edited | removed | false, once the user answered a Needs-you claim
    user_decision: Mapped[str | None] = mapped_column(String(12))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CompanyFact(Base):
    """A sentence about a company, verbatim from a fetched source, cached for a week."""

    __tablename__ = "company_facts"
    __table_args__ = (Index("ix_company_facts_key_time", "company_key", "retrieved_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_key: Mapped[str] = mapped_column(String(200))
    fact: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(1000))
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SkillPlan(Base):
    """A growth plan: one project that proves a skill the ledger lacks (spec section 7)."""

    __tablename__ = "skill_plans"
    __table_args__ = (UniqueConstraint("user_id", "skill_key", name="uq_skill_plans_user_skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    # The job whose gap started the plan; the plan outlives the job.
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    skill: Mapped[str] = mapped_column(String(100))  # taxonomy name, e.g. "Kubernetes"
    skill_key: Mapped[str] = mapped_column(String(100))  # ledger value, e.g. "kubernetes"
    title: Mapped[str] = mapped_column(String(200))
    why: Mapped[str] = mapped_column(Text)
    # [{"text": ..., "done": bool}]
    steps_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    hours: Mapped[int] = mapped_column(Integer)
    # open -> in_progress -> verified
    status: Mapped[str] = mapped_column(String(12), default="open")
    written_by: Mapped[str] = mapped_column(String(8))  # "llm" or "template"
    repo_url: Mapped[str | None] = mapped_column(String(300))
    # The last repo check, one row per acceptance criterion: [{"name", "passed", "detail"}]
    checks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ApplicationFile(Base):
    """A file the user added for an application (transcript, references, samples). Kept in the
    database: the platform's disk is not persistent, and these are small (size-capped) files."""

    __tablename__ = "application_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = _user_fk()
    kind: Mapped[str] = mapped_column(String(20))  # an attachment kind, e.g. "transcript"
    filename: Mapped[str] = mapped_column(String(120))
    content_type: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Audit(Base):
    __tablename__ = "audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    actor: Mapped[str] = mapped_column(String(40))  # "user" or an agent name
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
