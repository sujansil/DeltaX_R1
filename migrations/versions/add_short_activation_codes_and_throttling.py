"""Add shared activation verification throttling.

Revision ID: 0009_short_activation_codes
Revises: 0008_r1b_drafts_timeout
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_short_activation_codes"
down_revision = "0008_r1b_drafts_timeout"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "activation_throttles",
        sa.Column("identifier_digest", sa.String(length=64), primary_key=True),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("activation_throttles")
