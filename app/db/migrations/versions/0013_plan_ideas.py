"""plan ideas

Growth plans keep the Coach's project ideas, each downloadable as a one-page proposal.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-17 08:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("skill_plans") as batch_op:
        batch_op.add_column(
            sa.Column("ideas_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_plans") as batch_op:
        batch_op.drop_column("ideas_json")
