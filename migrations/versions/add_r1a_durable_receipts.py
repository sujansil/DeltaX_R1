"""add durable R1A receipt handoff

Revision ID: 0012_r1a_receipts
Revises: 0011_r1a_auto_result
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_r1a_receipts"
down_revision = "0011_r1a_auto_result"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "r1a_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("option_id", sa.Integer(), sa.ForeignKey("question_options.id", ondelete="CASCADE"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("question_position", sa.Integer(), nullable=True),
        sa.Column("question_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("question_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_code", sa.String(40), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'REJECTED')", name="ck_r1a_receipt_status"),
    )
    op.create_index("ix_r1a_receipts_team_id", "r1a_receipts", ["team_id"])
    op.create_index("ix_r1a_receipts_session_id", "r1a_receipts", ["session_id"])
    op.create_index("ix_r1a_receipts_received_at", "r1a_receipts", ["received_at"])
    op.create_index("ix_r1a_receipts_question_deadline", "r1a_receipts", ["question_deadline"])
    op.create_index("ix_r1a_receipts_pending_time", "r1a_receipts", ["received_at"],
                    postgresql_where=sa.text("status = 'PENDING'"))


def downgrade():
    op.drop_table("r1a_receipts")
