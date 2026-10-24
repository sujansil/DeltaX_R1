from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("login_username", sa.String(120), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("activation_token_hash", sa.String(64), unique=True),
        sa.Column("activation_expires_at", sa.DateTime(timezone=True)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_teams_code","teams",["code"],unique=True)
    op.create_index("ix_teams_login_username","teams",["login_username"],unique=True)

    op.create_table("admins",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("username",sa.String(120),nullable=False,unique=True),
        sa.Column("password_hash",sa.String(255),nullable=False),
        sa.Column("enabled",sa.Boolean(),nullable=False,server_default=sa.true()))
    op.create_index("ix_admins_username","admins",["username"],unique=True)

    op.create_table("rounds",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("code",sa.String(8),nullable=False,unique=True),
        sa.Column("status",sa.String(16),nullable=False),
        sa.Column("started_at",sa.DateTime(timezone=True)),
        sa.Column("ended_at",sa.DateTime(timezone=True)),
        sa.Column("leaderboard_published_at",sa.DateTime(timezone=True)))

    op.create_table("questions",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("round_code",sa.String(8),nullable=False),
        sa.Column("position",sa.Integer(),nullable=False),
        sa.Column("question_text",sa.Text()),
        sa.Column("question_image_url",sa.String(500)),
        sa.Column("answer_type",sa.String(20),nullable=False),
        sa.Column("correct_option_id",sa.Integer()),
        sa.Column("correct_numeric",sa.Numeric(30,12)),
        sa.Column("tolerance_abs",sa.Numeric(30,12),nullable=False,server_default="0"),
        sa.Column("tolerance_rel",sa.Numeric(30,12),nullable=False,server_default="0"),
        sa.Column("enabled",sa.Boolean(),nullable=False,server_default=sa.true()),
        sa.UniqueConstraint("round_code","position",name="uq_round_question_position"))
    op.create_index("ix_questions_round_code","questions",["round_code"])

    op.create_table("question_options",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("question_id",sa.Integer(),sa.ForeignKey("questions.id",ondelete="CASCADE"),nullable=False),
        sa.Column("position",sa.Integer(),nullable=False),
        sa.Column("option_text",sa.Text()),
        sa.Column("image_url",sa.String(500)),
        sa.UniqueConstraint("question_id","position",name="uq_question_option_position"))
    op.create_index("ix_question_options_question_id","question_options",["question_id"])

    op.create_foreign_key("fk_question_correct_option","questions","question_options",["correct_option_id"],["id"],ondelete="SET NULL")

    op.create_table("player_devices",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("team_id",sa.Integer(),sa.ForeignKey("teams.id",ondelete="CASCADE"),nullable=False),
        sa.Column("player_number",sa.Integer(),nullable=False),
        sa.Column("device_label",sa.String(120)),
        sa.Column("session_id",sa.String(64),nullable=False,unique=True),
        sa.Column("active",sa.Boolean(),nullable=False,server_default=sa.true()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("team_id",name="uq_one_session_per_team"))
    op.create_index("ix_player_devices_team_id","player_devices",["team_id"])

    op.create_table("team_round_states",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("team_id",sa.Integer(),sa.ForeignKey("teams.id",ondelete="CASCADE"),nullable=False),
        sa.Column("round_code",sa.String(8),nullable=False),
        sa.Column("current_player",sa.Integer()),
        sa.Column("current_question_position",sa.Integer()),
        sa.Column("turn_status",sa.String(24),nullable=False),
        sa.Column("team_started_at",sa.DateTime(timezone=True)),
        sa.Column("team_deadline",sa.DateTime(timezone=True)),
        sa.Column("ready_at",sa.DateTime(timezone=True)),
        sa.Column("finished_at",sa.DateTime(timezone=True)),
        sa.UniqueConstraint("team_id","round_code",name="uq_team_round"))

    op.create_table("submissions",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("team_id",sa.Integer(),sa.ForeignKey("teams.id",ondelete="CASCADE"),nullable=False),
        sa.Column("round_code",sa.String(8),nullable=False),
        sa.Column("question_position",sa.Integer(),nullable=False),
        sa.Column("player_number",sa.Integer()),
        sa.Column("answer_text",sa.Text()),
        sa.Column("selected_option_id",sa.Integer(),sa.ForeignKey("question_options.id")),
        sa.Column("submitted_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("elapsed_ms",sa.Integer()),
        sa.Column("result",sa.String(20),nullable=False),
        sa.Column("points",sa.Integer(),nullable=False),
        sa.UniqueConstraint("team_id","round_code","question_position",name="uq_team_round_question"))
    op.create_index("ix_submissions_team_id","submissions",["team_id"])
    op.create_index("ix_submissions_round_code","submissions",["round_code"])

    op.create_table("score_events",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("team_id",sa.Integer(),sa.ForeignKey("teams.id",ondelete="CASCADE"),nullable=False),
        sa.Column("round_code",sa.String(8),nullable=False),
        sa.Column("submission_id",sa.Integer(),sa.ForeignKey("submissions.id",ondelete="SET NULL")),
        sa.Column("reason",sa.String(80),nullable=False),
        sa.Column("points",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_score_events_team_id","score_events",["team_id"])
    op.create_index("ix_score_events_round_code","score_events",["round_code"])
    op.create_index("ix_score_team_round","score_events",["team_id","round_code"])

    op.create_table("leaderboard_snapshots",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("round_code",sa.String(8),nullable=False),
        sa.Column("published_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("data_json",sa.Text(),nullable=False))
    op.create_index("ix_leaderboard_snapshots_round_code","leaderboard_snapshots",["round_code"])

    op.create_table("admin_actions",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("admin_id",sa.Integer(),sa.ForeignKey("admins.id",ondelete="SET NULL")),
        sa.Column("action",sa.String(100),nullable=False),
        sa.Column("details",sa.Text()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))

def downgrade():
    for t in ["admin_actions","leaderboard_snapshots","score_events","submissions","team_round_states","player_devices","question_options","questions","rounds","admins","teams"]:
        op.drop_table(t)
