"""Email preferences reachable from an email itself: the digest unsubscribe link.

The signed token in the link is the only credential, so this works without signing in (from a
phone's mail app, for example). It is a POST: the page behind the email link asks first, and
mail clients send the RFC 8058 one-click POST from the List-Unsubscribe header.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import AppSettings, Db
from app.api.ratelimit import rate_limit
from app.db.models import Profile
from app.services import audit, unsubscribe

router = APIRouter(prefix="/api/email", tags=["email"])


@router.post(
    "/unsubscribe",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("lookup"))],
)
async def unsubscribe_digest(db: Db, settings: AppSettings, t: str = "") -> Response:
    user_id = unsubscribe.user_id(t, settings.secret_key.get_secret_value())
    if user_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This unsubscribe link has expired. Turn digests off in your profile instead.",
        )
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is not None and profile.digest_emails:
        profile.digest_emails = False
        audit.record(
            db,
            user_id=user_id,
            actor="user",
            action="digest_unsubscribed",
            entity="user",
            entity_id=user_id,
        )
    # The same answer for an account that no longer exists: nothing to stop.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
