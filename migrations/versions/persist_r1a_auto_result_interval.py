"""Persist the R1A automatic result interval deadline.

Revision ID: 0011_r1a_auto_result
Revises: 0010_competition_rules
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_r1a_auto_result"
down_revision = "0010_competition_rules"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rounds", sa.Column("r1a_result_due_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("rounds", "r1a_result_due_at")
