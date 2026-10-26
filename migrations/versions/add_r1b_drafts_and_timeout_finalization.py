"""Add versioned R1B drafts and durable timeout finalization metadata."""
from alembic import op
import sqlalchemy as sa


revision = "0008_r1b_drafts_timeout"
down_revision = "0007_r1a_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("team_round_states", sa.Column("current_turn_id", sa.String(36), nullable=True))
    op.add_column("team_round_states", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("team_round_states", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("team_round_states", sa.Column("finalization_source", sa.String(32), nullable=True))
    # Give an in-progress pre-migration ACTIVE turn a stable identity so it can
    # immediately use the draft API. Existing EXPIRED/FINISHED rows stay untouched.
    op.execute("""
        UPDATE team_round_states
        SET current_turn_id = substr(md5('r1b-turn-' || id::text), 1, 8) || '-' ||
                              substr(md5('r1b-turn-' || id::text), 9, 4) || '-' ||
                              substr(md5('r1b-turn-' || id::text), 13, 4) || '-' ||
                              substr(md5('r1b-turn-' || id::text), 17, 4) || '-' ||
                              substr(md5('r1b-turn-' || id::text), 21, 12)
        WHERE round_code = 'R1B' AND turn_status = 'ACTIVE'
    """)
    op.create_table(
        "r1b_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_position", sa.Integer(), nullable=False),
        sa.Column("player_number", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("team_id", "turn_id", name="uq_r1b_draft_turn"),
        sa.CheckConstraint("question_position BETWEEN 1 AND 4", name="ck_r1b_draft_question"),
        sa.CheckConstraint("player_number BETWEEN 1 AND 4", name="ck_r1b_draft_player"),
        sa.CheckConstraint("version >= 1", name="ck_r1b_draft_version"),
    )
    op.create_index("ix_r1b_drafts_team_id", "r1b_drafts", ["team_id"])
    op.add_column("submissions", sa.Column("finalization_source", sa.String(32), nullable=True))
    op.add_column("submissions", sa.Column("draft_id", sa.Integer(), nullable=True))
    op.add_column("submissions", sa.Column("draft_version", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_submissions_draft", "submissions", "r1b_drafts", ["draft_id"], ["id"], ondelete="SET NULL")
    op.execute("UPDATE submissions SET finalization_source = CASE WHEN result = 'SKIPPED' THEN 'SKIP' ELSE 'MANUAL' END WHERE round_code = 'R1B'")


def downgrade():
    op.drop_constraint("fk_submissions_draft", "submissions", type_="foreignkey")
    op.drop_column("submissions", "draft_version")
    op.drop_column("submissions", "draft_id")
    op.drop_column("submissions", "finalization_source")
    op.drop_index("ix_r1b_drafts_team_id", table_name="r1b_drafts")
    op.drop_table("r1b_drafts")
    op.drop_column("team_round_states", "finalization_source")
    op.drop_column("team_round_states", "finalized_at")
    op.drop_column("team_round_states", "expired_at")
    op.drop_column("team_round_states", "current_turn_id")
