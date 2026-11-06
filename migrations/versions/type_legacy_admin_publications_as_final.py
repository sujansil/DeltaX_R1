"""Type historical admin publication snapshots as explicit final results.

Before result-visibility controls, every list-shaped leaderboard snapshot was
created by the explicit admin Publish endpoint. Automatic R1A snapshots already
use a versioned object envelope. Convert only those historical lists so future
player reads never infer finality from an untyped snapshot.
"""
from alembic import op

revision = "0014_type_final_publications"
down_revision = "0013_shared_r1b_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      UPDATE leaderboard_snapshots
      SET data_json = jsonb_build_object(
        'kind', 'EXPLICIT_FINAL_PUBLICATION_V1',
        'rows', data_json::jsonb
      )::text
      WHERE jsonb_typeof(data_json::jsonb) = 'array'
    """)


def downgrade():
    op.execute("""
      UPDATE leaderboard_snapshots
      SET data_json = (data_json::jsonb -> 'rows')::text
      WHERE data_json::jsonb ->> 'kind' = 'EXPLICIT_FINAL_PUBLICATION_V1'
    """)
