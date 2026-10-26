"""Add round-scoped leased player sessions and revocation audit fields.

Existing session rows are retained. Their round is inferred from server-owned round
state and their lease is capped at 60 seconds from migration time, so a connected
browser can preserve the session by heartbeating while abandoned rows recover fast.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_round_sessions"
down_revision = "0005_r1b_timer_modes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("player_devices", sa.Column("round_code", sa.String(8), nullable=True))
    op.add_column("player_devices", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("player_devices", sa.Column("revoked_by_admin_id", sa.Integer(), nullable=True))
    op.add_column("player_devices", sa.Column("revocation_reason", sa.String(500), nullable=True))
    op.create_foreign_key("fk_player_devices_revoked_admin", "player_devices", "admins",
                          ["revoked_by_admin_id"], ["id"], ondelete="SET NULL")
    op.execute("""
        UPDATE player_devices SET
          round_code = CASE
            WHEN EXISTS (SELECT 1 FROM rounds WHERE code='R1B' AND status IN ('LIVE','PAUSED')) THEN 'R1B'
            WHEN EXISTS (SELECT 1 FROM rounds WHERE code='R1A' AND status IN ('DRAFT','LIVE','PAUSED')) THEN 'R1A'
            ELSE 'R1B'
          END,
          expires_at = LEAST(expires_at, CURRENT_TIMESTAMP + INTERVAL '60 seconds')
    """)
    op.alter_column("player_devices", "round_code", nullable=False)
    op.drop_constraint("uq_one_session_per_team", "player_devices", type_="unique")
    op.create_check_constraint("ck_player_devices_round_code", "player_devices", "round_code IN ('R1A', 'R1B')")
    op.create_check_constraint("ck_player_devices_slot", "player_devices", "player_number BETWEEN 1 AND 4")
    op.create_index("ix_player_devices_team_round", "player_devices", ["team_id", "round_code"])
    op.create_index("uq_active_team_round_slot", "player_devices",
                    ["team_id", "round_code", "player_number"], unique=True,
                    postgresql_where=sa.text("active IS TRUE"))


def downgrade():
    # Keep the newest row for each team before restoring the legacy one-row shape.
    op.execute("""
        DELETE FROM player_devices older USING player_devices newer
        WHERE older.team_id = newer.team_id AND older.id < newer.id
    """)
    op.drop_index("uq_active_team_round_slot", table_name="player_devices")
    op.drop_index("ix_player_devices_team_round", table_name="player_devices")
    op.drop_constraint("ck_player_devices_slot", "player_devices", type_="check")
    op.drop_constraint("ck_player_devices_round_code", "player_devices", type_="check")
    op.create_unique_constraint("uq_one_session_per_team", "player_devices", ["team_id"])
    op.drop_constraint("fk_player_devices_revoked_admin", "player_devices", type_="foreignkey")
    op.drop_column("player_devices", "revocation_reason")
    op.drop_column("player_devices", "revoked_by_admin_id")
    op.drop_column("player_devices", "revoked_at")
    op.drop_column("player_devices", "round_code")
