"""structured profile fields

Profile values are now normalised (E.164 phones, location keys, canonical LinkedIn URLs, enum
values, structured salary). Free-text values from earlier versions can't satisfy the new formats,
so they are cleared before the columns narrow; the user re-enters them through validated forms.
Target locations are JSON lists and are filtered when read, so they need no change here.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16 19:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLEAR_INVALID = """
UPDATE profiles SET
  phone = CASE WHEN phone LIKE '+%' AND length(phone) <= 20 THEN phone ELSE NULL END,
  location = CASE WHEN location = 'remote' OR location LIKE 'city:%' OR location LIKE 'country:%'
                  THEN location ELSE NULL END,
  linkedin = CASE WHEN length(linkedin) <= 200 AND linkedin LIKE 'https://www.linkedin.com/in/%'
                  THEN linkedin ELSE NULL END,
  work_authorization = NULL,
  notice_period = NULL
"""


def upgrade() -> None:
    op.execute(_CLEAR_INVALID)
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("salary_amount", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("salary_currency", sa.String(length=3), nullable=True))
        batch_op.add_column(sa.Column("salary_period", sa.String(length=10), nullable=True))
        batch_op.alter_column(
            "phone",
            existing_type=sa.VARCHAR(length=50),
            type_=sa.String(length=20),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "location",
            existing_type=sa.VARCHAR(length=200),
            type_=sa.String(length=40),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "linkedin",
            existing_type=sa.VARCHAR(length=500),
            type_=sa.String(length=200),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "work_authorization",
            existing_type=sa.VARCHAR(length=200),
            type_=sa.String(length=30),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "notice_period",
            existing_type=sa.VARCHAR(length=100),
            type_=sa.String(length=20),
            existing_nullable=True,
        )
        batch_op.drop_column("salary_expectation")


def downgrade() -> None:
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("salary_expectation", sa.VARCHAR(length=100), nullable=True))
        batch_op.alter_column(
            "notice_period",
            existing_type=sa.String(length=20),
            type_=sa.VARCHAR(length=100),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "work_authorization",
            existing_type=sa.String(length=30),
            type_=sa.VARCHAR(length=200),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "linkedin",
            existing_type=sa.String(length=200),
            type_=sa.VARCHAR(length=500),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "location",
            existing_type=sa.String(length=40),
            type_=sa.VARCHAR(length=200),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "phone",
            existing_type=sa.String(length=20),
            type_=sa.VARCHAR(length=50),
            existing_nullable=True,
        )
        batch_op.drop_column("salary_period")
        batch_op.drop_column("salary_currency")
        batch_op.drop_column("salary_amount")
