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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResumeLine(Base):
    __tablename__ = "resume_lines"
    __table_args__ = (Index("ix_resume_lines_user_version", "user_id", "resume_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    resume_version: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(40))
    line_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "type", "normalized_value", "source", name="uq_evidence_fact_source"
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClaimBlocklist(Base):
    __tablename__ = "claim_blocklist"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    normalized_value: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Audit(Base):
    __tablename__ = "audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = _user_fk()
    actor: Mapped[str] = mapped_column(String(40))  # "user" or an agent name
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
