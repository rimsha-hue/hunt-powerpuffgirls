"""Request and response contracts for the HTTP API."""

import re
from datetime import datetime
from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domain.profile import NoticePeriod, SalaryPeriod, WorkAuthorization
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
