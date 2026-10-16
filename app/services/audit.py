"""Audit trail. Every gate decision and every ledger change is recorded with ids, never content."""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Audit

logger = structlog.get_logger()


def record(
    db: AsyncSession,
    *,
    user_id: int,
    actor: str,
    action: str,
    entity: str,
    entity_id: int | str | None = None,
    **detail: Any,
) -> None:
    db.add(
        Audit(
            user_id=user_id,
            actor=actor,
            action=action,
            entity=entity,
            entity_id=None if entity_id is None else str(entity_id),
            detail_json=detail,
        )
    )
    logger.info(
        "audit", user_id=user_id, actor=actor, action=action, entity=entity, entity_id=entity_id
    )
