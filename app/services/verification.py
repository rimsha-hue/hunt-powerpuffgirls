"""Email ownership: verification links and sign-in alerts.

A verification link is a random 32-byte token stored only as its hash, single use and expiring,
like an Apply link. Until the user follows one (or signs in with Google, which checked the
address), Power Hunt sends no digest and no application from the account.
"""

import secrets
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EmailToken, User
from app.integrations.email import EmailError, EmailProvider, OutgoingEmail
from app.services import audit
from app.services.auth import hash_token

logger = structlog.get_logger()

VERIFY_EMAIL = "verify_email"


class VerificationError(Exception):
    """Safe to show to the user."""


def issue(db: AsyncSession, user: User, *, hours: int, now: datetime) -> str:
    """Stores a new token's hash; returns the raw token for the link."""
    token = secrets.token_urlsafe(32)
    db.add(
        EmailToken(
            user_id=user.id,
            purpose=VERIFY_EMAIL,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=now + timedelta(hours=hours),
        )
    )
    return token


def link(base_url: str, token: str) -> str:
    return f"{base_url}/api/auth/verify?t={token}"


async def confirm(db: AsyncSession, token: str, *, now: datetime) -> User:
    """Marks the email verified, or raises VerificationError (one message for every failure, so
    tokens can't be probed)."""
    row = (
        await db.execute(
            select(EmailToken).where(
                EmailToken.token_hash == hash_token(token), EmailToken.purpose == VERIFY_EMAIL
            )
        )
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise VerificationError("That link has expired or was already used. Ask for a new one.")
    user = await db.get(User, row.user_id)
    if user is None:
        raise VerificationError("That link has expired or was already used. Ask for a new one.")
    row.used_at = now
    user.email_verified_at = user.email_verified_at or now
    audit.record(
        db, user_id=user.id, actor="user", action="email_verified", entity="user", entity_id=user.id
    )
    return user


def verification_email(user: User, url: str, hours: int) -> OutgoingEmail:
    greeting = f"Hi {user.name}," if user.name else "Hi,"
    return OutgoingEmail(
        to=user.email,
        subject="Confirm your email for Power Hunt",
        text=(
            f"{greeting}\n\nConfirm this is your email so Power Hunt can send your job digests and "
            f"applications:\n\n{url}\n\nThe link works once and expires in {hours} hours. If you "
            "didn't sign up, ignore this email and nothing will be sent from the account."
        ),
    )


def login_alert(user: User, *, when: datetime, method: str, ip: str | None) -> OutgoingEmail:
    where = f" from {ip}" if ip else ""
    return OutgoingEmail(
        to=user.email,
        subject="New sign-in to your Power Hunt account",
        text=(
            f"Someone signed in to your Power Hunt account with {method}{where} at "
            f"{when:%d %B %Y, %H:%M} UTC.\n\nIf this was you, there's nothing to do. If not, "
            "change your password and sign out of other sessions."
        ),
    )


async def send_quietly(email: EmailProvider, message: OutgoingEmail, *, event: str) -> None:
    """For mail that must never break the request that triggered it (runs after the response)."""
    if not email.configured:
        logger.info(f"{event}_skipped", reason="email_not_configured")
        return
    try:
        await email.send(message)
    except EmailError as exc:
        logger.warning(f"{event}_failed", error_type=type(exc).__name__)
