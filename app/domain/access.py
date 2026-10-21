"""Who may do what. Two roles: a user owns their own data; an admin runs the service.

Admins manage accounts (suspend, restore, sign out, role, delete, resend verification) and see
system health and the audit log. They never see a user's resume, CV, ledger or email content:
the admin API has no endpoint that returns them (least privilege).

The rules below keep the service manageable: no admin can lock themselves out, and the last
admin can't be removed.
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal, get_args

Role = Literal["user", "admin"]
ROLES: tuple[Role, ...] = get_args(Role)
AdminAction = Literal["suspend", "restore", "sign_out", "set_role", "delete", "resend_verification"]


@dataclass(frozen=True)
class Account:
    id: int
    role: str
    suspended: bool
    verified: bool
    demo: bool


def is_admin(role: str, suspended: bool) -> bool:
    return role == "admin" and not suspended


def should_be_admin(email: str, verified: bool, role: str, admin_emails: Collection[str]) -> bool:
    """A listed email becomes an admin only once it is verified: anyone could sign up with it."""
    return role != "admin" and verified and email.lower() in admin_emails


def refusal(
    action: AdminAction,
    *,
    actor_id: int,
    target: Account,
    active_admins: int,
    new_role: Role | None = None,
) -> str | None:
    """Why an admin may not do this to `target`; None when allowed. `active_admins` counts
    unsuspended admins, the actor included."""
    own = actor_id == target.id
    if own and action in ("suspend", "delete"):
        return "Use another admin account for that; you can't do it to your own account here."
    if own and action == "set_role" and new_role != "admin":
        return "Another admin has to remove your admin role."
    removes_admin = is_admin(target.role, target.suspended) and (
        action in ("suspend", "delete") or (action == "set_role" and new_role != "admin")
    )
    if removes_admin and active_admins <= 1:
        return "That would leave the service without an admin."
    if action == "delete" and target.demo:
        return "The shared demo account can't be deleted."
    if action == "set_role" and new_role == "admin" and not target.verified:
        return "Only an account with a verified email can be an admin."
    if action == "set_role" and new_role == target.role:
        return f"That account is already a {target.role}."
    if action == "suspend" and target.suspended:
        return "That account is already suspended."
    if action == "restore" and not target.suspended:
        return "That account isn't suspended."
    if action == "resend_verification" and target.verified:
        return "That email is already verified."
    return None
