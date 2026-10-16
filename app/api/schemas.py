"""Request and response contracts for the HTTP API."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domain.profile import NoticePeriod, SalaryPeriod, WorkAuthorization
from app.domain.status import JobStatus
from app.integrations.github import USERNAME_RE
from app.services.auth import MIN_PASSWORD_LENGTH

ShortText = Annotated[str, Field(max_length=100)]


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    name: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    # Login only looks the address up; validating its format here could lock out an account
    # that exists (the seeded demo account, for example).
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    github_username: str | None
    portfolio_url: str | None


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str
    onboarded: bool
    calm_mode: bool
    is_demo: bool
    # Set when the session came from a signed Apply link: it opens this one job only.
    scope_job_id: int | None = None


class PlaceOut(BaseModel):
    key: str
    label: str
    kind: str


class SalaryIn(BaseModel):
    amount: int = Field(gt=0, le=1_000_000_000)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    period: SalaryPeriod

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class SalaryOut(BaseModel):
    amount: int
    currency: str
    period: SalaryPeriod


class ProfileOut(BaseModel):
    name: str
    email: str
    github_username: str | None
    portfolio_url: str | None
    phone: str | None
    location: PlaceOut | None
    linkedin: str | None
    work_authorization: WorkAuthorization | None
    notice_period: NoticePeriod | None
    salary: SalaryOut | None
    target_roles: list[str]
    target_locations: list[PlaceOut]
    match_threshold: int
    calm_mode: bool
    resume_version: int


class ProfileIn(BaseModel):
    """Shape checks only. Meaning (is it a real place, a job title, a phone number?) is checked by
    the profile service against reference data, and every field is normalised before storage.
    An explicit null clears a field; an omitted field is left unchanged."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    github_username: str | None = Field(default=None, max_length=60)
    portfolio_url: AnyHttpUrl | None = None
    phone: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=100)
    linkedin: str | None = Field(default=None, max_length=300)
    work_authorization: WorkAuthorization | None = None
    notice_period: NoticePeriod | None = None
    salary: SalaryIn | None = None
    target_roles: list[ShortText] | None = Field(default=None, max_length=10)
    target_locations: list[ShortText] | None = Field(default=None, max_length=10)
    match_threshold: int | None = Field(default=None, ge=0, le=100)
    calm_mode: bool | None = None

    @field_validator("github_username")
    @classmethod
    def _github(cls, value: str | None) -> str | None:
        if not value:
            return None
        value = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", value.strip()).strip("/")
        if not USERNAME_RE.match(value):
            raise ValueError("Not a valid GitHub username")
        return value


class Option(BaseModel):
    value: str
    label: str


class PublicConfigOut(BaseModel):
    """Server limits and choices the UI needs, so the frontend never duplicates them."""

    max_upload_mb: int
    google_sign_in: bool
    work_authorization: list[Option]
    notice_period: list[Option]
    salary_period: list[Option]


class EvidenceOut(BaseModel):
    id: int
    claim_text: str
    type: str
    normalized_value: str
    source: str
    source_ref: str
    source_label: str
    source_detail: str | None
    source_url: str | None
    context: str | None
    confirmed_by: str
    confidence: float
    created_at: datetime


class ResumeLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_no: int
    section: str
    text: str


class ResumeIngestionOut(BaseModel):
    resume_version: int
    line_count: int
    facts: list[EvidenceOut]
    rejected_by_code: int
    blocked: int
    llm_used: bool
    llm_note: str | None


class GitHubIngestionOut(BaseModel):
    username: str
    repos_scanned: int
    facts: list[EvidenceOut]


class PortfolioIngestionOut(BaseModel):
    url: str
    method: str  # "direct" or "rendered" (JavaScript page read through Jina Reader)
    line_count: int
    facts: list[EvidenceOut]
    rejected_by_code: int
    llm_note: str | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    title: str
    company: str
    location: str
    remote: bool
    url: str
    posted_at: datetime | None
    fetched_at: datetime
    apply_method: str
    apply_email: str | None
    apply_url: str | None
    required_attachments: list[str]
    match_score: int
    ats_before: int
    matched_skills: list[str]
    missing_skills: list[str]
    status: str
    status_changed_at: datetime


class KeywordOut(BaseModel):
    term: str
    kind: str
    weight: int
    found: bool


class JobDetailOut(JobOut):
    description: str
    keywords: list[KeywordOut]
    ats_checks: dict[str, bool]
    ats_coverage: float


class JobStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: JobStatus


class SourceOutcomeOut(BaseModel):
    name: str
    fetched: int
    error: str | None


class ScoutRunOut(BaseModel):
    queries: int
    fetched: int
    relevant: int
    duplicates: int
    new_jobs: int
    rescored: int
    used_recorded: bool
    sources: list[SourceOutcomeOut]
    digest_sent: int
    digest_note: str | None


class CvBlockOut(BaseModel):
    kind: str
    section: str
    text: str
    original: str
    line_no: int
    entry: int
    rewritten: bool


class ClaimSourceOut(BaseModel):
    label: str  # "Resume", "GitHub", "Confirmed by you", "Your resume line"
    detail: str | None
    url: str | None


class ClaimOut(BaseModel):
    id: int
    document: str  # cv, email or cover_letter
    block_index: int
    text: str
    type: str
    span_start: int
    span_end: int
    bucket: str
    origin: str
    similarity: float | None
    user_decision: str | None
    source: ClaimSourceOut | None


class AtsAfterOut(BaseModel):
    before: int
    after: int
    matched: list[str]
    missing: list[str]
    checks: dict[str, bool]


class ApplicationOut(BaseModel):
    id: int
    job_id: int
    status: str
    method: str
    note: str | None
    blocks: list[CvBlockOut]
    claims: list[ClaimOut]
    ats: AtsAfterOut
    questions_asked: int
    needs_you: int
    checked_automatically: int
    blocked: int
    cv_approved_at: datetime | None
    updated_at: datetime
    email: "EmailOut | None"


class AttachmentOut(BaseModel):
    kind: str
    label: str
    status: str  # attached | link | uploaded | missing | waived
    detail: str | None


class EmailOut(BaseModel):
    recipient: str | None
    subject: str | None
    blocks: list[CvBlockOut]
    signature: str
    cover_letter: list[CvBlockOut]
    attachments: list[AttachmentOut]
    note: str | None
    blockers: list[str]
    sent_at: datetime | None
    follow_up_due: datetime | None


class EmailEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient: EmailStr | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=150)
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    cover_letter: str | None = Field(default=None, min_length=1, max_length=6000)


class WaiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    waived: bool


class ClaimDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "edit", "remove", "false"]
    url: AnyHttpUrl | None = None
    note: str | None = Field(default=None, max_length=280)
    text: str | None = Field(default=None, max_length=300)
