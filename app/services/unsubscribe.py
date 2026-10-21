"""Unsubscribe links for digest emails.

The token is signed with the app secret (no database row) and names only the account. The link in
the email body opens a page that asks before turning digests off, because mail scanners follow
links; mail clients use the one-click POST from the List-Unsubscribe header (RFC 8058).
"""

from app.services import signing

PURPOSE = "unsubscribe_digest"
SECONDS_PER_DAY = 86_400


def token(user_id: int, secret: str, days: int) -> str:
    return signing.sign({"uid": user_id}, secret, days * SECONDS_PER_DAY, purpose=PURPOSE)


def user_id(token: str, secret: str) -> int | None:
    payload = signing.unsign(token, secret, purpose=PURPOSE)
    uid = payload.get("uid") if payload else None
    return uid if isinstance(uid, int) else None


def page_link(base_url: str, token: str) -> str:
    return f"{base_url}/unsubscribe?t={token}"


def headers(base_url: str, token: str) -> tuple[tuple[str, str], ...]:
    one_click = f"{base_url}/api/email/unsubscribe?t={token}"
    return (
        ("List-Unsubscribe", f"<{one_click}>"),
        ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
    )
