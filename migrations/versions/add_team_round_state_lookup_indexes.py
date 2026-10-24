"""Add lookup indexes declared by the ORM metadata."""
from alembic import op


revision = "0004_team_round_state_indexes"
down_revision = "0003_pause_and_presence"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_player_devices_session_id", "player_devices", ["session_id"], unique=True)
    op.create_index("ix_team_round_states_round_code", "team_round_states", ["round_code"])
    op.create_index("ix_team_round_states_team_id", "team_round_states", ["team_id"])


def downgrade():
    op.drop_index("ix_team_round_states_team_id", table_name="team_round_states")
    op.drop_index("ix_team_round_states_round_code", table_name="team_round_states")
    op.drop_index("ix_player_devices_session_id", table_name="player_devices")
