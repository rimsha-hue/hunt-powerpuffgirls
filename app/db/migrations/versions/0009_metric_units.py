"""metric units by head noun (Phase 7)

A count's unit is now the noun it measures, past modifiers: "1,200 support agents" is keyed
"1200 agent" (was "1200 support"), "2M daily records" "2000000 record" (was "2000000 daily").
Stored metric evidence is recomputed with the current rules, so claims keep matching the ledger.
Rows that end up equal to another row of the same user and source are merged.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17 10:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.domain.ledger import is_version_number, normalize_fact
from app.domain.normalize import find_quantities
from app.domain.taxonomy import default_taxonomy

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

evidence = sa.table(
    "evidence",
    sa.column("id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("type", sa.String),
    sa.column("normalized_value", sa.String),
    sa.column("source", sa.String),
    sa.column("claim_text", sa.Text),
)


def _resume_key(text: str) -> str | None:
    """As ingestion keys a metric clause (`ledger._metric_facts`): every unit-bearing quantity."""
    keys = {
        q.key
        for q in find_quantities(text)
        if q.unit not in {"", "year-date"}
        and (q.unit.startswith("gpa/") or not is_version_number(text, q))
    }
    return "|".join(sorted(keys)) or None


def upgrade() -> None:
    bind = op.get_bind()
    taxonomy = default_taxonomy()
    rows = bind.execute(
        sa.select(
            evidence.c.id,
            evidence.c.user_id,
            evidence.c.source,
            evidence.c.normalized_value,
            evidence.c.claim_text,
        )
        .where(evidence.c.type == "metric")
        .order_by(evidence.c.id)
    ).all()
    seen: set[tuple[int, str, str]] = set()
    duplicates: list[int] = []
    changes: dict[int, str] = {}
    for row in rows:
        if row.source == "user_confirmed":
            value = normalize_fact("metric", row.claim_text, taxonomy)
        else:
            value = _resume_key(row.claim_text)
        value = (value or row.normalized_value)[:500]
        key = (row.user_id, row.source, value)
        if key in seen:
            duplicates.append(row.id)
        else:
            seen.add(key)
            if value != row.normalized_value:
                changes[row.id] = value
    if duplicates:
        bind.execute(sa.delete(evidence).where(evidence.c.id.in_(duplicates)))
    # Through unique placeholders first, so one row taking another's old value never collides.
    for row_id in changes:
        bind.execute(
            sa.update(evidence)
            .where(evidence.c.id == row_id)
            .values(normalized_value=f"migrating:{row_id}")
        )
    for row_id, value in changes.items():
        bind.execute(
            sa.update(evidence).where(evidence.c.id == row_id).values(normalized_value=value)
        )


def downgrade() -> None:
    # The old keys can't be rebuilt from the new ones; re-uploading the resume restores them.
    pass
