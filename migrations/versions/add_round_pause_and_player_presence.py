"""Add frozen-round pause state and player presence timestamp."""
from alembic import op
import sqlalchemy as sa

revision = "0003_pause_and_presence"
down_revision = "0002_manual_questions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rounds", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("player_devices", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_player_devices_last_seen_at", "player_devices", ["last_seen_at"])


def downgrade():
    op.drop_index("ix_player_devices_last_seen_at", table_name="player_devices")
    op.drop_column("player_devices", "last_seen_at")
    op.drop_column("rounds", "paused_at")
