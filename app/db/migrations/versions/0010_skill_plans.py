"""skill plans (Phase 6)

Growth plans: one project per skill the ledger can't prove, with its steps, the linked repo and
the result of the last repo check.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-17 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("skill", sa.String(length=100), nullable=False),
        sa.Column("skill_key", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("hours", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("written_by", sa.String(length=8), nullable=False),
        sa.Column("repo_url", sa.String(length=300), nullable=True),
        sa.Column("checks_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "skill_key", name="uq_skill_plans_user_skill"),
    )
    op.create_index(op.f("ix_skill_plans_user_id"), "skill_plans", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_skill_plans_user_id"), table_name="skill_plans")
    op.drop_table("skill_plans")
