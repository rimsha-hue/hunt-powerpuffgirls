"""Job status: the one place that decides which status changes are allowed (spec section 12).

new -> shortlisted -> in_review -> approved -> applied -> {interview, assessment, offer, rejected},
plus expired (the posting closed) and skipped (the user dismissed it, reversible).
"""

from datetime import datetime
from typing import Literal, TypeGuard, get_args

JobStatus = Literal[
    "new",
    "shortlisted",
    "in_review",
    "approved",
    "applied",
    "interview",
    "assessment",
    "offer",
    "rejected",
    "expired",
    "skipped",
]
JOB_STATUSES: tuple[JobStatus, ...] = get_args(JobStatus)

_OUTCOMES: frozenset[JobStatus] = frozenset({"interview", "assessment", "offer", "rejected"})

TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    "new": frozenset({"shortlisted", "skipped", "expired"}),
    "shortlisted": frozenset({"in_review", "skipped", "expired"}),
    "in_review": frozenset({"approved", "shortlisted", "skipped", "expired"}),
    "approved": frozenset({"applied", "in_review", "skipped", "expired"}),
    "applied": _OUTCOMES,
    "interview": _OUTCOMES - {"interview"},
    "assessment": _OUTCOMES - {"assessment"},
    "offer": frozenset({"rejected"}),
    "rejected": frozenset(),
    "expired": frozenset(),
    # A dismissed job can be brought back.
    "skipped": frozenset({"new"}),
}
# "expired" is final for Power Hunt's own flow; applied_elsewhere() is the one way out of it.

# Jobs the user can still act on; used for listing defaults and digests.
OPEN_STATUSES: frozenset[JobStatus] = frozenset({"new", "shortlisted", "in_review", "approved"})


class InvalidTransitionError(ValueError):
    """Safe to show to the user."""


def is_status(value: str) -> TypeGuard[JobStatus]:
    return value in TRANSITIONS


def transition(current: str, target: str) -> JobStatus:
    """The new status, or InvalidTransitionError when the change isn't allowed."""
    if not is_status(current) or not is_status(target):
        raise InvalidTransitionError(f"Unknown job status: {target!r}.")
    if target not in TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"A job that is {current.replace('_', ' ')} can't move to {target.replace('_', ' ')}."
        )
    return target


def applied_elsewhere(current: str) -> JobStatus:
    """The user applied on their own, outside Power Hunt, and wants to track the job.

    Separate from `transition` on purpose: inside Power Hunt an application only reaches
    "applied" through the gates, and that table keeps refusing shortcuts. This is the user's own
    statement about something Power Hunt never sent. A closed posting can still be marked, since
    the user may have applied before it closed."""
    if current not in OPEN_STATUSES and current != "expired":
        raise InvalidTransitionError(
            f"A job that is {current.replace('_', ' ')} can't be marked as applied."
        )
    return "applied"


# ---- The applications pipeline, as the user sees it --------------------------------------------

Stage = Literal[
    "picked",
    "tailoring",
    "ready",
    "applied",
    "follow_up",
    "interview",
    "assessment",
    "offer",
    "rejected",
    "closed",
]
STAGES: tuple[Stage, ...] = get_args(Stage)
_OUTCOME_STAGES: dict[str, Stage] = {
    "interview": "interview",
    "assessment": "assessment",
    "offer": "offer",
    "rejected": "rejected",
}


def pipeline_stage(
    job_status: str, application_status: str | None, follow_up_due: datetime | None, now: datetime
) -> Stage:
    """One stage per job from the job's status and its application's progress. An application
    that went out and has waited past its follow-up date is "follow_up": the user should nudge."""
    if job_status in _OUTCOME_STAGES:
        return _OUTCOME_STAGES[job_status]
    if job_status == "applied" or application_status in ("sent", "submitted"):
        return "follow_up" if follow_up_due is not None and follow_up_due <= now else "applied"
    if job_status == "expired":
        return "closed"
    if job_status == "approved" or application_status == "cv_approved":
        return "ready"
    if job_status == "in_review" or application_status == "draft":
        return "tailoring"
    return "picked"
