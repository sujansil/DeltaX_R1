"""Add stable round roster and explicit timeout outcome timestamps."""
from alembic import op
import sqlalchemy as sa


revision = "0007_r1a_reconciliation"
down_revision = "0006_round_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "round_enrollments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_code", sa.String(8), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("team_id", "round_code", name="uq_round_enrollment"),
        sa.CheckConstraint("round_code IN ('R1A', 'R1B')", name="ck_round_enrollment_code"),
    )
    op.create_index("ix_round_enrollments_team_id", "round_enrollments", ["team_id"])
    op.create_index("ix_round_enrollments_round_code", "round_enrollments", ["round_code"])
    # An already-started deployment has no historical enrolment data. Snapshot the
    # enabled teams at migration time once; subsequent starts snapshot atomically.
    op.execute("""
        INSERT INTO round_enrollments (team_id, round_code, enrolled_at)
        SELECT teams.id, 'R1A', CURRENT_TIMESTAMP
        FROM teams
        WHERE teams.enabled
          AND EXISTS (SELECT 1 FROM rounds WHERE code = 'R1A' AND started_at IS NOT NULL)
        ON CONFLICT DO NOTHING
    """)
    op.alter_column("submissions", "submitted_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.add_column("submissions", sa.Column("timed_out_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("submissions", sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE submissions SET recorded_at = submitted_at")
    op.alter_column("submissions", "recorded_at", nullable=False)
    op.create_index(
        "uq_score_submission", "score_events", ["submission_id"], unique=True,
        postgresql_where=sa.text("submission_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_score_submission", table_name="score_events")
    op.execute("UPDATE submissions SET submitted_at = recorded_at WHERE submitted_at IS NULL")
    op.drop_column("submissions", "recorded_at")
    op.drop_column("submissions", "timed_out_at")
    op.alter_column("submissions", "submitted_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.drop_index("ix_round_enrollments_round_code", table_name="round_enrollments")
    op.drop_index("ix_round_enrollments_team_id", table_name="round_enrollments")
    op.drop_table("round_enrollments")
