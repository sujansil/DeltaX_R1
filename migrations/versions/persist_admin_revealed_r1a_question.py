"""Persist the question explicitly revealed by the admin."""
from alembic import op
import sqlalchemy as sa

revision = "0002_manual_questions"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rounds", sa.Column("current_position", sa.Integer(), nullable=True))
    op.add_column("rounds", sa.Column("question_started_at", sa.DateTime(timezone=True), nullable=True))
    # Preserve progress of an already-running old automatic round at migration time.
    op.execute("""
        UPDATE rounds SET
          current_position = LEAST(23, GREATEST(1,
            FLOOR(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - started_at)) / 30)::integer + 1)),
          question_started_at = started_at + INTERVAL '30 seconds' * LEAST(22, GREATEST(0,
            FLOOR(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - started_at)) / 30)::integer))
        WHERE code = 'R1A' AND status = 'LIVE' AND started_at IS NOT NULL
    """)


def downgrade():
    op.drop_column("rounds", "question_started_at")
    op.drop_column("rounds", "current_position")
