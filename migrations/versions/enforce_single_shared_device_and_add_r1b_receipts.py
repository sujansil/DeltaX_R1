"""Enforce one shared R1B device and add durable relay-turn receipts.

For legacy duplicate active sessions, retain the lowest-id unexpired session per
team/round (the oldest stable identity) and revoke every other active row. Expired
rows are first reclaimed. Competition state, drafts, submissions, and scores are
not modified. Existing JWT claims are never rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_shared_r1b_receipts"
down_revision = "0012_r1a_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      UPDATE player_devices
      SET active = FALSE
      WHERE active IS TRUE AND expires_at <= CURRENT_TIMESTAMP
    """)
    op.execute("""
      WITH ranked AS (
        SELECT id, row_number() OVER (
          PARTITION BY team_id, round_code ORDER BY id ASC
        ) AS keep_rank
        FROM player_devices WHERE active IS TRUE
      )
      UPDATE player_devices AS device
      SET active = FALSE,
          revoked_at = CURRENT_TIMESTAMP,
          revocation_reason = 'Migration to one shared team device; oldest active session retained'
      FROM ranked
      WHERE device.id = ranked.id AND ranked.keep_rank > 1
    """)
    op.drop_index("uq_active_team_round_slot", table_name="player_devices")
    op.create_index("uq_active_team_round", "player_devices", ["team_id", "round_code"],
                    unique=True, postgresql_where=sa.text("active IS TRUE"))
    op.create_table(
        "r1b_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("question_position", sa.Integer(), nullable=False),
        sa.Column("player_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("turn_ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("team_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_code", sa.String(40), nullable=True),
        sa.CheckConstraint("action IN ('SUBMIT', 'SKIP')", name="ck_r1b_receipt_action"),
        sa.CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'REJECTED')", name="ck_r1b_receipt_status"),
    )
    for column in ("team_id", "session_id", "turn_id", "received_at", "team_deadline"):
        op.create_index(f"ix_r1b_receipts_{column}", "r1b_receipts", [column])
    op.create_index("ix_r1b_receipts_pending_deadline", "r1b_receipts", ["team_deadline"],
                    postgresql_where=sa.text("status = 'PENDING'"))


def downgrade():
    op.drop_table("r1b_receipts")
    op.drop_index("uq_active_team_round", table_name="player_devices")
    op.create_index("uq_active_team_round_slot", "player_devices",
                    ["team_id", "round_code", "player_number"], unique=True,
                    postgresql_where=sa.text("active IS TRUE"))
