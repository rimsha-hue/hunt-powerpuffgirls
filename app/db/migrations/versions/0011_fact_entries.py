"""fact entries

A fact is unique per source and entry, so the same role at two companies (or one school for two
degrees) keeps both links. Claims remember the role or degree an organisation was stated with.

Resume facts get their entry from the stored resume lines, by the current rules. Portfolio facts
don't keep their page lines, so they get entries on the next portfolio sync; schools appear on
the next resume upload.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-17 06:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.domain.ledger import ENTRY_BOUND_TYPES, MAX_CONTEXT_LENGTH, entry_contexts
from app.domain.resume import ParsedLine

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

evidence = sa.table(
    "evidence",
    sa.column("id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("type", sa.String),
    sa.column("source", sa.String),
    sa.column("source_ref", sa.String),
    sa.column("context", sa.String),
)
resume_lines = sa.table(
    "resume_lines",
    sa.column("id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("section", sa.String),
    sa.column("line_no", sa.Integer),
    sa.column("text", sa.Text),
    sa.column("bulleted", sa.Boolean),
)


def _backfill_entries() -> None:
    bind = op.get_bind()
    lines: dict[int, list[tuple[int, ParsedLine]]] = {}
    for row in bind.execute(
        sa.select(resume_lines).order_by(resume_lines.c.user_id, resume_lines.c.line_no)
    ):
        parsed = ParsedLine(row.line_no, row.section, row.text, bool(row.bulleted))
        lines.setdefault(row.user_id, []).append((row.id, parsed))
    entry_by_line_id: dict[int, str | None] = {}
    for user_lines in lines.values():
        contexts = entry_contexts(parsed for _, parsed in user_lines)
        entry_by_line_id.update({i: contexts.get(p.line_no) for i, p in user_lines})
    rows = bind.execute(
        sa.select(evidence.c.id, evidence.c.source_ref, evidence.c.context).where(
            evidence.c.source == "resume", evidence.c.type.in_(sorted(ENTRY_BOUND_TYPES))
        )
    ).all()
    for row in rows:
        if not row.source_ref.isdigit():
            continue
        entry = entry_by_line_id.get(int(row.source_ref))
        entry = entry[:MAX_CONTEXT_LENGTH] if entry else None
        if entry != row.context:
            bind.execute(sa.update(evidence).where(evidence.c.id == row.id).values(context=entry))


def upgrade() -> None:
    with op.batch_alter_table("evidence") as batch_op:
        batch_op.drop_constraint("uq_evidence_fact_source", type_="unique")
    op.create_index(
        "uq_evidence_fact_source_entry",
        "evidence",
        ["user_id", "type", "normalized_value", "source", sa.text("coalesce(context, '')")],
        unique=True,
    )
    # Rows were unique per (user, type, value, source) before, so giving them entries can't collide.
    _backfill_entries()
    with op.batch_alter_table("claims") as batch_op:
        batch_op.add_column(sa.Column("pair_type", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("pair_value", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("pair_text", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("claims") as batch_op:
        batch_op.drop_column("pair_text")
        batch_op.drop_column("pair_value")
        batch_op.drop_column("pair_type")
    op.drop_index("uq_evidence_fact_source_entry", table_name="evidence")
    # Keep one row per (user, type, value, source) so the old constraint can come back.
    op.execute(
        "DELETE FROM evidence WHERE id NOT IN (SELECT MIN(id) FROM evidence "
        "GROUP BY user_id, type, normalized_value, source)"
    )
    with op.batch_alter_table("evidence") as batch_op:
        batch_op.create_unique_constraint(
            "uq_evidence_fact_source", ["user_id", "type", "normalized_value", "source"]
        )
