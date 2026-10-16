"""email apply (Phase 4)

Email body and cover letter as verified sentence blocks, the recipient, files the user adds
for an application, and which document each claim belongs to (existing claims are CV claims).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17 02:35:30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.String(length=120), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("application_files", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_application_files_application_id"), ["application_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_application_files_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "email_blocks_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
            )
        )
        batch_op.add_column(
            sa.Column(
                "cover_letter_blocks_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(sa.Column("recipient", sa.String(length=320), nullable=True))
        batch_op.add_column(sa.Column("compose_note", sa.String(length=500), nullable=True))

    with op.batch_alter_table("claims", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("document", sa.String(length=12), nullable=False, server_default="cv")
        )


def downgrade() -> None:
    with op.batch_alter_table("claims", schema=None) as batch_op:
        batch_op.drop_column("document")

    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_column("compose_note")
        batch_op.drop_column("recipient")
        batch_op.drop_column("cover_letter_blocks_json")
        batch_op.drop_column("email_blocks_json")

    with op.batch_alter_table("application_files", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_application_files_user_id"))
        batch_op.drop_index(batch_op.f("ix_application_files_application_id"))

    op.drop_table("application_files")
