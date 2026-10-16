"""Password hashing (argon2) and server-side sessions with per-session CSRF tokens."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AuthSession, Profile, User

_hasher = PasswordHasher()
# Verified against when the email is unknown, so response time doesn't reveal which emails exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(expected: str, provided: str | None) -> bool:
    return provided is not None and hmac.compare_digest(expected.encode(), provided.encode())


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(
        select(User).where(User.email == email.strip().lower()).options(selectinload(User.profile))
    )
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password: str | None,
    name: str = "",
    google_sub: str | None = None,
) -> User:
    """`password=None` creates an account with no password (it signs in through Google only)."""
    user = User(
        email=email.strip().lower(),
        password_hash=hash_password(password) if password is not None else "",
        name=name.strip(),
        google_sub=google_sub,
    )
    user.profile = Profile()
    db.add(user)
    await db.flush()
    return user


async def sign_in_with_google(
    db: AsyncSession, *, sub: str, email: str, name: str
) -> tuple[User, bool]:
    """(user, created). Match by Google account id, then by the verified email, else create.

    Linking by email is safe because Google has verified the address: whoever signs in owns it.
    """
    result = await db.execute(
        select(User).where(User.google_sub == sub).options(selectinload(User.profile))
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user, False
    user = await get_user_by_email(db, email)
    if user is not None:
        user.google_sub = sub
        if not user.name and name:
            user.name = name
        return user, False
    return await create_user(db, email=email, password=None, name=name, google_sub=sub), True


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(db, email)
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


async def create_session(
    db: AsyncSession, user: User, *, days: int, scope_job_id: int | None = None
) -> tuple[str, AuthSession]:
    """Returns the raw token (for the cookie) and the stored session (holding only its hash).

    The user's expired sessions are removed at the same time, so the table can't grow without bound.
    """
    await db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id, AuthSession.expires_at <= datetime.now(UTC)
        )
    )
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=secrets.token_urlsafe(32),
        scope_job_id=scope_job_id,
        expires_at=datetime.now(UTC) + timedelta(days=days),
    )
    db.add(session)
    await db.flush()
    return token, session


async def resolve_session(db: AsyncSession, token: str) -> tuple[AuthSession, User] | None:
    result = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_token(token))
        .options(selectinload(User.profile))
    )
    row = result.first()
    if row is None:
        return None
    session, user = row
    expires = (
        session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=UTC)
    )
    if expires <= datetime.now(UTC):
        await db.delete(session)
        return None
    return session, user


async def end_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
