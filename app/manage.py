"""Operator commands, run where the app's environment is set (a server shell or `docker exec`).

    python -m app.manage role <email> admin     make an account an admin (its email must be verified)
    python -m app.manage role <email> user      make an admin a regular user again
    python -m app.manage admins                 list the admins

Roles never change through the web UI's own account pages; this and the admin console (and
ADMIN_EMAILS) are the only ways, and each change is audited.
"""

import asyncio
import sys
from collections.abc import Sequence

from sqlalchemy import select

from app.config import get_settings
from app.db.models import User
from app.db.session import create_engine, create_session_factory
from app.domain.access import ROLES
from app.services import audit
from app.services.auth import get_user_by_email


async def set_role(email: str, role: str) -> str:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        async with create_session_factory(engine)() as db:
            user = await get_user_by_email(db, email)
            if user is None:
                return f"No account uses {email}."
            if role == "admin" and not user.email_verified:
                return "That account hasn't verified its email yet."
            if user.role == role:
                return f"{email} is already a {role}."
            user.role = role
            audit.record(
                db,
                user_id=user.id,
                actor="system",
                action="admin_granted" if role == "admin" else "admin_set_role",
                entity="user",
                entity_id=user.id,
                reason="manage_command",
            )
            await db.commit()
            return f"{email} is now a {role}."
    finally:
        await engine.dispose()


async def list_admins() -> str:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        async with create_session_factory(engine)() as db:
            emails = (await db.execute(select(User.email).where(User.role == "admin"))).scalars()
            return "\n".join(emails) or "No admins yet."
    finally:
        await engine.dispose()


def main(argv: Sequence[str]) -> int:
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    if len(argv) == 3 and argv[0] == "role" and argv[2] in ROLES:
        print(asyncio.run(set_role(argv[1], argv[2]), loop_factory=loop))
        return 0
    if argv == ["admins"]:
        print(asyncio.run(list_admins(), loop_factory=loop))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
