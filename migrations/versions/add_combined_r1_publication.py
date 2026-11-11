"""One immutable final combined Round 1 publication per run.

This adds schema only. No teams, questions, credentials, scores or existing
subround publications are changed. Authorized rehearsal reset may invalidate a
combined publication by deleting it, allowing the next rehearsal to publish.
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_combined_r1_publication"
down_revision = "0014_type_final_publications"
branch_labels = None
depends_on = None


IMMUTABILITY_FUNCTION_SQL = """
CREATE FUNCTION protect_combined_r1_snapshot() RETURNS trigger AS $$
BEGIN
  IF OLD.round_code = 'R1' OR NEW.round_code = 'R1' THEN
    RAISE EXCEPTION 'Published combined Round 1 standings are immutable'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

IMMUTABILITY_TRIGGER_SQL = """
CREATE TRIGGER combined_r1_snapshot_immutable
BEFORE UPDATE ON leaderboard_snapshots
FOR EACH ROW EXECUTE FUNCTION protect_combined_r1_snapshot()
"""


def upgrade():
    op.create_index("uq_combined_r1_publication", "leaderboard_snapshots", ["round_code"],
                    unique=True, postgresql_where=sa.text("round_code = 'R1'"))
    op.execute(IMMUTABILITY_FUNCTION_SQL)
    op.execute(IMMUTABILITY_TRIGGER_SQL)


def downgrade():
    op.execute("DROP TRIGGER combined_r1_snapshot_immutable ON leaderboard_snapshots")
    op.execute("DROP FUNCTION protect_combined_r1_snapshot()")
    op.drop_index("uq_combined_r1_publication", table_name="leaderboard_snapshots")
