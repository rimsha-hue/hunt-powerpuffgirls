"""applications and claims (Phase 3)

The tailored CV of each application and every claim in it, with how it was routed. Resume
lines gain a bullet flag; lines stored before this migration count as not bulleted until the
resume is uploaded again.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17 01:45:27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("tailored_cv_json", sa.JSON(), nullable=False),
        sa.Column("tailor_note", sa.String(length=300), nullable=True),
        sa.Column("resume_version", sa.Integer(), nullable=False),
        sa.Column("ats_after", sa.Integer(), nullable=False),
        sa.Column("ats_after_detail_json", sa.JSON(), nullable=False),
        sa.Column("questions_asked", sa.Integer(), nullable=False),
        sa.Column("cv_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cover_letter", sa.Text(), nullable=True),
        sa.Column("email_subject", sa.String(length=300), nullable=True),
        sa.Column("email_body", sa.Text(), nullable=True),
        sa.Column("attachments_json", sa.JSON(), nullable=False),
        sa.Column("answers_json", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_id", sa.String(length=300), nullable=True),
        sa.Column("follow_up_due", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_applications_job"),
    )
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_applications_user_id"), ["user_id"], unique=False)

    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("normalized_value", sa.String(length=500), nullable=False),
        sa.Column("span_start", sa.Integer(), nullable=False),
        sa.Column("span_end", sa.Integer(), nullable=False),
        sa.Column("bucket", sa.String(length=12), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=True),
        sa.Column("cited_line_id", sa.Integer(), nullable=True),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("user_decision", sa.String(length=12), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cited_line_id"], ["resume_lines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("claims", schema=None) as batch_op:
        batch_op.create_index(
            "ix_claims_application_bucket", ["application_id", "bucket"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_claims_application_id"), ["application_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_claims_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("resume_lines", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("bulleted", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("resume_lines", schema=None) as batch_op:
        batch_op.drop_column("bulleted")

    with op.batch_alter_table("claims", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_claims_user_id"))
        batch_op.drop_index(batch_op.f("ix_claims_application_id"))
        batch_op.drop_index("ix_claims_application_bucket")

    op.drop_table("claims")
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_applications_user_id"))

    op.drop_table("applications")
