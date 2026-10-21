"""account emails

A welcome email sent once per account, a switch for digest emails, and a log of every email sent
for a user (kind and time only) that caps digests and applications per day.

Accounts that were verified before this change count as welcomed, so they get no late welcome.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-21 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("welcomed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE users SET welcomed_at = email_verified_at WHERE email_verified_at IS NOT NULL"
    )
    with op.batch_alter_table("profiles") as batch_op:
        batch_op.add_column(
            sa.Column("digest_emails", sa.Boolean(), nullable=False, server_default=sa.true())
        )
    op.create_table(
        "email_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_log_user_time", "email_log", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_email_log_user_time", table_name="email_log")
    op.drop_table("email_log")
    with op.batch_alter_table("profiles") as batch_op:
        batch_op.drop_column("digest_emails")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("welcomed_at")
