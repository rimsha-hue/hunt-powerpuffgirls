"""form apply (Phase 5)

Company facts cached per company, where an application's form questions came from, and the
user's saved answers to questions the profile has no field for.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17 02:56:13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_key", sa.String(length=200), nullable=False),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("company_facts", schema=None) as batch_op:
        batch_op.create_index(
            "ix_company_facts_key_time", ["company_key", "retrieved_at"], unique=False
        )

    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("form_source", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("form_url", sa.String(length=1000), nullable=True))

    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("saved_answers", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.drop_column("saved_answers")

    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_column("form_url")
        batch_op.drop_column("form_source")

    with op.batch_alter_table("company_facts", schema=None) as batch_op:
        batch_op.drop_index("ix_company_facts_key_time")

    op.drop_table("company_facts")
