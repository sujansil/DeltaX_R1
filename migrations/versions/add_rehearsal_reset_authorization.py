"""Add an empty operator-provisioned rehearsal safety attestation.

No existing database is authorized by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_rehearsal_reset_guard"
down_revision = "0015_combined_r1_publication"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "round1_rehearsal_authorization",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("database_name", sa.String(128), nullable=False),
        sa.Column("database_oid", sa.BigInteger(), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_rehearsal_authorization_singleton"),
    )


def downgrade():
    op.drop_table("round1_rehearsal_authorization")
