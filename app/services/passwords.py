"""Forgot, reset and change password.

A reset link is a random 32-byte token stored only as its hash, single use and short lived; a new
request cancels any earlier link. Resetting or changing the password signs out every other
session, and the account gets an email saying the password changed, so a takeover can't go
unnoticed.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthSession, EmailToken, User
from app.integrations.email import OutgoingEmail
from app.services import audit
from app.services.auth import hash_password, verify_password
from app.services.verification import consume, issue_token

RESET_PASSWORD = "reset_password"


class PasswordError(Exception):
    """Safe to show to the user."""


async def issue_reset(db: AsyncSession, user: User, *, minutes: int, now: datetime) -> str:
    """A reset token; earlier unused ones stop working."""
    await db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == RESET_PASSWORD,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    return issue_token(
        db, user, purpose=RESET_PASSWORD, lifetime=timedelta(minutes=minutes), now=now
    )


def reset_link(base_url: str, token: str) -> str:
    # The frontend page asks for the new password and posts it with the token.
    return f"{base_url}/reset-password?t={token}"


async def _sign_out(db: AsyncSession, user_id: int, keep: int | None = None) -> None:
    query = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep is not None:
        query = query.where(AuthSession.id != keep)
    await db.execute(query)


async def reset(db: AsyncSession, token: str, password: str, *, now: datetime) -> User:
    used = await consume(db, token, purpose=RESET_PASSWORD, now=now)
    if used is None:
        raise PasswordError("That link has expired or was already used. Ask for a new one.")
    _, user = used
    user.password_hash = hash_password(password)
    # The link arrived in this inbox, so the address is proven.
    user.email_verified_at = user.email_verified_at or now
    await _sign_out(db, user.id)
    audit.record(
        db, user_id=user.id, actor="user", action="password_reset", entity="user", entity_id=user.id
    )
    return user


async def change(
    db: AsyncSession,
    user: User,
    *,
    current: str,
    new: str,
    session_id: int,
    now: datetime,
) -> None:
    """Needs the current password when the account has one; keeps only this session."""
    if user.password_hash and not verify_password(user.password_hash, current):
        raise PasswordError("Your current password is wrong.")
    if user.password_hash and current == new:
        raise PasswordError("Choose a password different from your current one.")
    user.password_hash = hash_password(new)
    await _sign_out(db, user.id, keep=session_id)
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="password_changed",
        entity="user",
        entity_id=user.id,
    )


def reset_email(user: User, url: str, minutes: int) -> OutgoingEmail:
    greeting = f"Hi {user.name}," if user.name else "Hi,"
    return OutgoingEmail(
        to=user.email,
        subject="Reset your Power Hunt password",
        text=(
            f"{greeting}\n\nSomeone asked to reset the password for your Power Hunt account. To "
            f"choose a new one, open this link:\n\n{url}\n\nIt works once and expires in "
            f"{minutes} minutes. If you didn't ask for this, ignore this email; your password "
            "stays the same."
        ),
    )


def changed_email(user: User, *, when: datetime) -> OutgoingEmail:
    return OutgoingEmail(
        to=user.email,
        subject="Your Power Hunt password was changed",
        text=(
            f"The password for your Power Hunt account was changed on {when:%d %B %Y, %H:%M} UTC, "
            "and every other device was signed out.\n\nIf this was you, there's nothing to do. "
            "If not, reset your password now from the sign-in page and contact us."
        ),
    )
