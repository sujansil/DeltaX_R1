"""snapshot effective competition rules per round

Revision ID: 0010_competition_rules
Revises: 0009_short_activation_codes
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "0010_competition_rules"
down_revision = "0009_short_activation_codes"
branch_labels = None
depends_on = None

LEGACY = {
    "version": "legacy-hardcoded-v1",
    "r1a": {"question_count": 23, "question_duration_ms": 30000,
            "full_score_until_ms": 10000, "decay_end_ms": 25000,
            "maximum_correct_score": 1000, "minimum_correct_score": 250,
            "wrong_answer_score": -500, "unanswered_score": 0},
    "r1b": {"turn_count": 4, "total_answering_budget_ms": 1680000,
            "correct_score": 4000, "wrong_score": -1500,
            "skip_unanswered_score": 0, "default_timer_mode": "CONTINUOUS"},
}

def upgrade():
    op.alter_column("team_round_states", "remaining_time_ms", server_default=None)
    op.add_column("rounds", sa.Column("rules_version", sa.String(120), nullable=True))
    op.add_column("rounds", sa.Column("rules_snapshot", sa.Text(), nullable=True))
    op.add_column("rounds", sa.Column("r1b_timer_mode_selected", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    payload = json.dumps(LEGACY, sort_keys=True, separators=(",", ":"))
    op.execute(sa.text("""UPDATE rounds SET rules_version=:version, rules_snapshot=:payload
                           WHERE status <> 'DRAFT' OR started_at IS NOT NULL""").bindparams(
        version=LEGACY["version"], payload=payload))
    op.execute("UPDATE rounds SET r1b_timer_mode_selected=true WHERE status <> 'DRAFT' OR started_at IS NOT NULL")

def downgrade():
    op.alter_column("team_round_states", "remaining_time_ms", server_default="1680000")
    op.drop_column("rounds", "r1b_timer_mode_selected")
    op.drop_column("rounds", "rules_snapshot")
    op.drop_column("rounds", "rules_version")
