"""Job status: the one place that decides which status changes are allowed (spec section 12).

new -> shortlisted -> in_review -> approved -> applied -> {interview, assessment, offer, rejected},
plus expired (the posting closed) and skipped (the user dismissed it, reversible).
"""

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
