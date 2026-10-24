from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Text, Numeric, UniqueConstraint, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

def now():
    return datetime.now(timezone.utc)

class RoundCode(str, Enum):
    R1A = "R1A"
    R1B = "R1B"

class RoundStatus(str, Enum):
    DRAFT = "DRAFT"
    LIVE = "LIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"

class R1BTimerMode(str, Enum):
    CONTINUOUS = "CONTINUOUS"
    PAUSE_DURING_HANDOVER = "PAUSE_DURING_HANDOVER"

class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    login_username: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=True)
    activation_token_hash: Mapped[str] = mapped_column(String(64), nullable=True, unique=True)
    activation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("code"),
        UniqueConstraint("login_username"),
        Index("ix_teams_code", "code", unique=True),
        Index("ix_teams_login_username", "login_username", unique=True),
    )

class PlayerDevice(Base):
    __tablename__ = "player_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    player_number: Mapped[int] = mapped_column(Integer, default=1)
    device_label: Mapped[str] = mapped_column(String(120), nullable=True)
    session_id: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("team_id", name="uq_one_session_per_team"),
        UniqueConstraint("session_id"),
        Index("ix_player_devices_session_id", "session_id", unique=True),
    )

class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("username"),
        Index("ix_admins_username", "username", unique=True),
    )

class Round(Base):
    __tablename__ = "rounds"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=RoundStatus.DRAFT.value)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leaderboard_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    r1b_timer_mode: Mapped[str] = mapped_column(
        String(32), default=R1BTimerMode.CONTINUOUS.value,
        server_default=R1BTimerMode.CONTINUOUS.value,
    )
    __table_args__ = (
        CheckConstraint(
            "r1b_timer_mode IN ('CONTINUOUS', 'PAUSE_DURING_HANDOVER')",
            name="ck_rounds_r1b_timer_mode",
        ),
    )

class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    round_code: Mapped[str] = mapped_column(String(8), index=True)
    position: Mapped[int] = mapped_column(Integer)
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    answer_type: Mapped[str] = mapped_column(String(20))  # MCQ / NUMERIC
    correct_option_id: Mapped[int | None] = mapped_column(ForeignKey("question_options.id", ondelete="SET NULL"), nullable=True)
    correct_numeric: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    tolerance_abs: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal("0"))
    tolerance_rel: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal("0"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("round_code", "position", name="uq_round_question_position"),)

class QuestionOption(Base):
    __tablename__ = "question_options"
    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    option_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    __table_args__ = (UniqueConstraint("question_id", "position", name="uq_question_option_position"),)

class TeamRoundState(Base):
    __tablename__ = "team_round_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    round_code: Mapped[str] = mapped_column(String(8), index=True)
    current_player: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_question_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turn_status: Mapped[str] = mapped_column(String(24), default="WAITING")
    team_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    team_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remaining_time_ms: Mapped[int] = mapped_column(
        Integer, default=28 * 60 * 1000, server_default=str(28 * 60 * 1000)
    )
    clock_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clock_running_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("team_id", "round_code", name="uq_team_round"),
        CheckConstraint("remaining_time_ms >= 0", name="ck_team_round_remaining_nonnegative"),
    )

class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    round_code: Mapped[str] = mapped_column(String(8), index=True)
    question_position: Mapped[int] = mapped_column(Integer)
    player_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_text: Mapped[str] = mapped_column(Text, nullable=True)
    selected_option_id: Mapped[int] = mapped_column(ForeignKey("question_options.id"), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(String(20))  # CORRECT/WRONG/UNANSWERED/SKIPPED
    points: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("team_id", "round_code", "question_position", name="uq_team_round_question"),)

class ScoreEvent(Base):
    __tablename__ = "score_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    round_code: Mapped[str] = mapped_column(String(8), index=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(String(80))
    points: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (Index("ix_score_team_round", "team_id", "round_code"),)

class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    round_code: Mapped[str] = mapped_column(String(8), index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data_json: Mapped[str] = mapped_column(Text)

class AdminAction(Base):
    __tablename__ = "admin_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
