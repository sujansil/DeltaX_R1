"""Add selectable R1B timer modes and persisted remaining-clock state."""
from alembic import op
import sqlalchemy as sa


revision = "0005_r1b_timer_modes"
down_revision = "0004_team_round_state_indexes"
branch_labels = None
depends_on = None

BUDGET_MS = 28 * 60 * 1000


def upgrade():
    op.add_column("rounds", sa.Column(
        "r1b_timer_mode", sa.String(32), nullable=False,
        server_default="CONTINUOUS",
    ))
    op.create_check_constraint(
        "ck_rounds_r1b_timer_mode", "rounds",
        "r1b_timer_mode IN ('CONTINUOUS', 'PAUSE_DURING_HANDOVER')",
    )

    op.add_column("team_round_states", sa.Column(
        "remaining_time_ms", sa.Integer(), nullable=False,
        server_default=str(BUDGET_MS),
    ))
    op.add_column("team_round_states", sa.Column(
        "clock_started_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("team_round_states", sa.Column(
        "clock_running_since", sa.DateTime(timezone=True), nullable=True,
    ))
    op.create_check_constraint(
        "ck_team_round_remaining_nonnegative", "team_round_states",
        "remaining_time_ms >= 0",
    )

    # Preserve the exact remaining budget represented by legacy absolute deadlines.
    # For globally paused R1B, paused_at is the freeze instant; otherwise migration
    # time is the reference. Existing clocks retain their consumed time rather than
    # being reset to the new first-READY semantics.
    op.execute(f"""
        UPDATE team_round_states AS state
        SET
          remaining_time_ms = CASE
            WHEN state.turn_status = 'EXPIRED' THEN 0
            WHEN state.team_deadline IS NULL THEN {BUDGET_MS}
            ELSE GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (
              state.team_deadline - CASE
                WHEN round.status = 'PAUSED' AND round.paused_at IS NOT NULL
                  THEN round.paused_at
                ELSE CURRENT_TIMESTAMP
              END
            )) * 1000)::integer)
          END,
          clock_started_at = COALESCE(state.ready_at, state.team_started_at),
          clock_running_since = CASE
            WHEN state.turn_status NOT IN ('FINISHED', 'EXPIRED')
                 AND state.team_deadline IS NOT NULL
                 AND round.status IN ('LIVE', 'PAUSED')
              THEN CASE
                WHEN round.status = 'PAUSED' AND round.paused_at IS NOT NULL
                  THEN round.paused_at
                ELSE CURRENT_TIMESTAMP
              END
            ELSE NULL
          END,
          team_deadline = CASE
            WHEN state.turn_status NOT IN ('FINISHED', 'EXPIRED')
                 AND state.team_deadline IS NOT NULL
                 AND round.status IN ('LIVE', 'PAUSED')
              THEN state.team_deadline
            ELSE NULL
          END
        FROM rounds AS round
        WHERE state.round_code = 'R1B' AND round.code = 'R1B'
    """)


def downgrade():
    op.drop_constraint("ck_team_round_remaining_nonnegative", "team_round_states", type_="check")
    op.drop_column("team_round_states", "clock_running_since")
    op.drop_column("team_round_states", "clock_started_at")
    op.drop_column("team_round_states", "remaining_time_ms")
    op.drop_constraint("ck_rounds_r1b_timer_mode", "rounds", type_="check")
    op.drop_column("rounds", "r1b_timer_mode")
