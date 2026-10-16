"""jobs, apply tokens and notifications (Phase 2)

Jobs found by the Scout, with frozen ATS keywords and scores; signed single-use Apply links
(hash only); and a record of each digest email sent.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16 23:16:57
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("dedupe_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=False),
        sa.Column("remote", sa.Boolean(), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("apply_method", sa.String(length=10), nullable=False),
        sa.Column("apply_email", sa.String(length=320), nullable=True),
        sa.Column("apply_url", sa.String(length=1000), nullable=True),
        sa.Column("required_attachments", sa.JSON(), nullable=False),
        sa.Column("ats_keywords_json", sa.JSON(), nullable=False),
        sa.Column("ats_before", sa.Integer(), nullable=False),
        sa.Column("ats_detail_json", sa.JSON(), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=False),
        sa.Column("matched_skills", sa.JSON(), nullable=False),
        sa.Column("missing_skills", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "dedupe_hash", name="uq_jobs_dedupe_hash"),
        sa.UniqueConstraint("user_id", "source", "external_id", name="uq_jobs_source_id"),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_user_id"), ["user_id"], unique=False)
        batch_op.create_index("ix_jobs_user_status", ["user_id", "status"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_ids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notifications_user_id"), ["user_id"], unique=False)

    op.create_table(
        "apply_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("apply_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_apply_tokens_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_apply_tokens_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("apply_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_apply_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_apply_tokens_job_id"))

    op.drop_table("apply_tokens")
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notifications_user_id"))

    op.drop_table("notifications")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_user_status")
        batch_op.drop_index(batch_op.f("ix_jobs_user_id"))

    op.drop_table("jobs")
