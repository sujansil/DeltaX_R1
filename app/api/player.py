"""One-token player bootstrap contract for refresh and reconnect."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..dependencies import require_player
from ..models import (PlayerDevice, Question, QuestionOption, R1BDraft, Round,
                      RoundStatus, ScoreEvent, Submission, Team, TeamRoundState)
from ..player_sessions import authorize_session
from ..r1a_reconciliation import reconcile_r1a
from ..r1b_finalization import effective_remaining_ms, finalize_expired_team
from ..round_locking import lock_round
from ..timing import remaining_ms, utc_now
from ..question_reveal import closed_position, visible_total, read_leaderboard
from ..competition_rules import round_rules

router = APIRouter(prefix="/api/player", tags=["player-bootstrap"])
settings = get_settings()


def coded_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


class IdentityOut(BaseModel):
    team_id: int
    team_code: str
    team_name: str
    session_id: str
    round_code: Literal["R1A", "R1B"]
    relay_slot: int
    lease_expires_at: datetime
    token_expires_at: int | None = None
    heartbeat_interval_seconds: int


class RoundOut(BaseModel):
    code: Literal["R1A", "R1B"]
    status: Literal["DRAFT", "LIVE", "PAUSED", "ENDED"]
    started_at: datetime | None = None
    paused_at: datetime | None = None
    ended_at: datetime | None = None


class TimerOut(BaseModel):
    mode: Literal["QUESTION", "CONTINUOUS", "PAUSE_DURING_HANDOVER"] | None = None
    running: bool
    frozen: bool
    remaining_ms: int
    deadline: datetime | None = None


class OptionOut(BaseModel):
    id: int
    position: int
    text: str | None = None
    image_url: str | None = None


class QuestionOut(BaseModel):
    id: int
    position: int
    text: str | None = None
    image_url: str | None = None
    options: list[OptionOut] | None = None


class OwnOutcomeOut(BaseModel):
    accepted: bool
    finalized: bool
    score_revealed: bool = False
    submission_id: int | None = None
    question_position: int | None = None
    player_number: int | None = None
    selected_option_id: int | None = None
    answer_text: str | None = None
    result: str | None = None
    points: int | None = None
    submitted_at: datetime | None = None
    timed_out_at: datetime | None = None
    recorded_at: datetime | None = None
    finalization_source: str | None = None


class DraftOut(BaseModel):
    turn_id: str
    version: int
    answer: str
    accepted_at: datetime | None = None


class PlayerStateOut(BaseModel):
    screen: Literal["NOT_STARTED", "ACTIVE_UNANSWERED", "ACTIVE_ACCEPTED",
                    "BETWEEN_QUESTIONS", "ACTIVE", "WAITING_TURN",
                    "HANDOVER_READY", "PAUSED", "FINISHED", "EXPIRED", "ENDED"]
    current_player: int | None = None
    current_question_position: int | None = None
    turn_status: str | None = None
    team_started_at: datetime | None = None
    clock_started_at: datetime | None = None
    ready_at: datetime | None = None
    finished_at: datetime | None = None
    expired_at: datetime | None = None
    finalized_at: datetime | None = None
    finalization_source: str | None = None


class BootstrapOut(BaseModel):
    contract_version: Literal["1"] = "1"
    snapshot_version: str
    snapshot_at: datetime
    identity: IdentityOut
    round: RoundOut
    rules: dict
    player_state: PlayerStateOut
    timer: TimerOut
    question: QuestionOut | None = None
    own_outcome: OwnOutcomeOut
    draft: DraftOut | None = None
    score_total: int = 0
    leaderboard: dict | None = None
    available_actions: list[Literal["START", "READY", "SUBMIT", "SKIP", "SAVE_DRAFT"]]


async def _authorized_device(db: AsyncSession, user: dict):
    scoped = await db.scalar(select(PlayerDevice).where(
        PlayerDevice.session_id == user.get("sid"),
        PlayerDevice.team_id == user.get("team_id"),
    ))
    if scoped and (scoped.round_code != user.get("round_code")
                   or scoped.player_number != user.get("slot")):
        raise coded_error(403, "ROUND_SCOPE_MISMATCH",
                          "Token round or relay slot does not match its session")
    try:
        return await authorize_session(db, user, user["round_code"])
    except HTTPException as exc:
        if exc.status_code == 403:
            raise coded_error(403, "ROUND_SCOPE_MISMATCH", "This token belongs to another round")
        raise coded_error(401, "SESSION_INACTIVE", "Session expired or revoked; log in again")


async def _reconcile_if_due(db: AsyncSession, user: dict) -> None:
    """Run a write transaction only when this token's persisted deadline is due."""
    now = utc_now()
    if user["round_code"] == "R1A":
        round_obj = await db.scalar(select(Round).where(Round.code == "R1A"))
        due = bool(
            round_obj and round_obj.status == RoundStatus.LIVE.value
            and round_obj.question_started_at
            and now >= round_obj.question_started_at + timedelta(
                milliseconds=round_rules(round_obj).r1a.question_duration_ms)
        )
        await db.rollback()
        if due:
            round_obj = await lock_round(db, "R1A", exclusive=True)
            await _authorized_device(db, user)
            await reconcile_r1a(db, now=utc_now(), locked_round=round_obj)
            await db.commit()
        return

    round_obj = await db.scalar(select(Round).where(Round.code == "R1B"))
    state = await db.scalar(select(TeamRoundState).where(
        TeamRoundState.team_id == user["team_id"], TeamRoundState.round_code == "R1B"
    ))
    due = bool(
        round_obj and round_obj.status == RoundStatus.LIVE.value and state
        and state.turn_status in ("ACTIVE", "WAITING")
        and state.clock_running_since is not None and state.team_deadline is not None
        and now >= state.team_deadline
    )
    await db.rollback()
    if due:
        round_obj = await lock_round(db, "R1B", exclusive=False)
        await _authorized_device(db, user)
        state = await db.scalar(select(TeamRoundState).where(
            TeamRoundState.team_id == user["team_id"],
            TeamRoundState.round_code == "R1B",
        ).with_for_update())
        if state:
            await finalize_expired_team(db, round_obj, state, utc_now())
        await db.commit()


def _outcome(submission: Submission | None) -> OwnOutcomeOut:
    if not submission:
        return OwnOutcomeOut(accepted=False, finalized=False)
    return OwnOutcomeOut(
        accepted=submission.submitted_at is not None,
        finalized=True, score_revealed=True,
        submission_id=submission.id,
        question_position=submission.question_position,
        player_number=submission.player_number,
        selected_option_id=submission.selected_option_id,
        answer_text=submission.answer_text,
        result=submission.result,
        points=submission.points,
        submitted_at=submission.submitted_at,
        timed_out_at=submission.timed_out_at,
        recorded_at=submission.recorded_at,
        finalization_source=submission.finalization_source,
    )


def _snapshot_version(parts: list[object]) -> str:
    encoded = json.dumps(parts, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


async def _question_out(db: AsyncSession, round_code: str, position: int) -> QuestionOut | None:
    question = await db.scalar(select(Question).where(
        Question.round_code == round_code, Question.position == position,
        Question.enabled.is_(True),
    ))
    if not question:
        return None
    options = None
    if round_code == "R1A":
        rows = (await db.scalars(select(QuestionOption).where(
            QuestionOption.question_id == question.id
        ).order_by(QuestionOption.position))).all()
        options = [OptionOut(id=o.id, position=o.position, text=o.option_text,
                             image_url=o.image_url) for o in rows]
    return QuestionOut(id=question.id, position=position, text=question.question_text,
                       image_url=question.question_image_url, options=options)


@router.get("/bootstrap", response_model=BootstrapOut,
            responses={401: {"description": "SESSION_INACTIVE"},
                       403: {"description": "ROUND_SCOPE_MISMATCH"},
                       409: {"description": "ROUND_NOT_CONFIGURED"}})
async def bootstrap(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    if user.get("round_code") not in ("R1A", "R1B"):
        raise coded_error(403, "ROUND_SCOPE_MISMATCH", "Token has no supported round scope")
    await _reconcile_if_due(db, user)

    # All following reads share one PostgreSQL MVCC snapshot. snapshot_at lets a
    # client discard a response older than one it has already rendered.
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    device = await _authorized_device(db, user)
    team = await db.scalar(select(Team).where(Team.id == device.team_id))
    round_obj = await db.scalar(select(Round).where(Round.code == device.round_code))
    if not round_obj:
        raise coded_error(409, "ROUND_NOT_CONFIGURED", "The session round is not configured")
    now = utc_now()
    rules = round_rules(round_obj)

    identity = IdentityOut(
        team_id=team.id, team_code=team.code, team_name=team.name,
        session_id=device.session_id, round_code=device.round_code,
        relay_slot=device.player_number, lease_expires_at=device.expires_at,
        token_expires_at=user.get("exp"),
        heartbeat_interval_seconds=settings.player_heartbeat_seconds,
    )
    round_out = RoundOut(code=round_obj.code, status=round_obj.status,
                         started_at=round_obj.started_at, paused_at=round_obj.paused_at,
                         ended_at=round_obj.ended_at)

    if device.round_code == "R1A":
        position = round_obj.current_position
        deadline = (round_obj.question_started_at + timedelta(milliseconds=rules.r1a.question_duration_ms)
                    if round_obj.question_started_at and position else None)
        active = bool(round_obj.status == "LIVE" and deadline and now < deadline)
        paused_active = bool(round_obj.status == "PAUSED" and deadline and round_obj.paused_at
                             and round_obj.paused_at < deadline)
        timer_reference = round_obj.paused_at if paused_active else now
        timer_remaining = remaining_ms(deadline, timer_reference) if deadline else 0
        submission = None
        if position:
            submission = await db.scalar(select(Submission).where(
                Submission.team_id == team.id, Submission.round_code == "R1A",
                Submission.question_position == position,
            ))
        if round_obj.status == "DRAFT":
            screen = "NOT_STARTED"
        elif round_obj.status == "ENDED":
            screen = "ENDED"
        elif round_obj.status == "PAUSED":
            screen = "PAUSED"
        elif active and submission:
            screen = "ACTIVE_ACCEPTED"
        elif active:
            screen = "ACTIVE_UNANSWERED"
        else:
            screen = "BETWEEN_QUESTIONS"
        question = await _question_out(db, "R1A", position) if active and not submission else None
        if active and not submission and question is None:
            raise coded_error(503, "QUESTION_NOT_CONFIGURED", "The current question is not configured")
        outcome = _outcome(submission)
        if submission and submission.question_position > closed_position(round_obj, now):
            outcome.score_revealed = False
            outcome.result = None
            outcome.points = None
        actions = ["SUBMIT"] if active and not submission else []
        player_state = PlayerStateOut(screen=screen, current_question_position=position)
        timer = TimerOut(mode="QUESTION", running=active, frozen=paused_active,
                         remaining_ms=timer_remaining,
                         deadline=deadline if active else None)
        version_parts = [round_obj.status, position, round_obj.question_started_at,
                         round_obj.paused_at, round_obj.ended_at,
                         submission.id if submission else None,
                         submission.recorded_at if submission else None]
    else:
        state = await db.scalar(select(TeamRoundState).where(
            TeamRoundState.team_id == team.id, TeamRoundState.round_code == "R1B"
        ))
        submission = await db.scalar(select(Submission).where(
            Submission.team_id == team.id, Submission.round_code == "R1B",
            Submission.player_number == device.player_number,
        ).order_by(Submission.question_position.desc()).limit(1))
        question = None
        draft_out = None
        actions = []
        if not state:
            turn_status, current_player, position = "NOT_STARTED", 1, 1
            remaining = rules.r1b.total_answering_budget_ms
            running = False
            screen = "ENDED" if round_obj.status == "ENDED" else "NOT_STARTED"
            if round_obj.status == "LIVE" and device.player_number == 1:
                actions = ["START"]
        else:
            turn_status, current_player = state.turn_status, state.current_player
            position = state.current_question_position
            reference = (round_obj.paused_at if round_obj.status == "PAUSED"
                         and round_obj.paused_at else now)
            remaining = effective_remaining_ms(state, reference)
            running = bool(round_obj.status == "LIVE" and state.clock_running_since
                           and turn_status not in ("FINISHED", "EXPIRED"))
            owns_turn = device.player_number == current_player
            if round_obj.status == "ENDED":
                screen = "ENDED"
            elif turn_status == "FINISHED":
                screen = "FINISHED"
            elif turn_status == "EXPIRED":
                screen = "EXPIRED"
            elif round_obj.status == "PAUSED":
                screen = "PAUSED"
            elif turn_status == "NOT_STARTED":
                screen = "NOT_STARTED"
                if owns_turn:
                    actions = ["START"]
            elif turn_status == "WAITING":
                screen = "HANDOVER_READY" if owns_turn else "WAITING_TURN"
                if owns_turn:
                    actions = ["READY"]
            elif turn_status == "ACTIVE" and owns_turn:
                screen = "ACTIVE"
                actions = ["SUBMIT", "SKIP", "SAVE_DRAFT"]
                question = await _question_out(db, "R1B", position)
                if question is None:
                    raise coded_error(503, "QUESTION_NOT_CONFIGURED", "The current question is not configured")
            else:
                screen = "WAITING_TURN"

            if turn_status == "ACTIVE" and owns_turn and state.current_turn_id:
                draft = await db.scalar(select(R1BDraft).where(
                    R1BDraft.team_id == team.id, R1BDraft.turn_id == state.current_turn_id,
                ))
                draft_out = DraftOut(
                    turn_id=state.current_turn_id, version=draft.version if draft else 0,
                    answer=draft.answer_text if draft else "",
                    accepted_at=draft.accepted_at if draft else None,
                )

        outcome = _outcome(submission)
        player_state = PlayerStateOut(
            screen=screen, current_player=current_player,
            current_question_position=position, turn_status=turn_status,
            team_started_at=state.team_started_at if state else None,
            clock_started_at=state.clock_started_at if state else None,
            ready_at=state.ready_at if state else None,
            finished_at=state.finished_at if state else None,
            expired_at=state.expired_at if state else None,
            finalized_at=state.finalized_at if state else None,
            finalization_source=state.finalization_source if state else None,
        )
        frozen = bool(round_obj.status == "PAUSED" or
                      (state and state.turn_status == "WAITING" and not state.clock_running_since))
        timer = TimerOut(
            mode=round_obj.r1b_timer_mode, running=running, frozen=frozen,
            remaining_ms=remaining,
            deadline=state.team_deadline if state and running else None,
        )
        version_parts = [round_obj.status, round_obj.paused_at, round_obj.ended_at,
                         state.id if state else None, turn_status, current_player, position,
                         state.current_turn_id if state else None,
                         state.finalized_at if state else None,
                         submission.id if submission else None,
                         draft_out.version if draft_out else None]

    score_total = await visible_total(db, team.id, device.round_code,
                                      closed_position(round_obj, now))
    leaderboard_out = await read_leaderboard(db, device.round_code, team.id)
    version_parts.extend([score_total, leaderboard_out.get("publication_id")])
    response = BootstrapOut(
        snapshot_version=_snapshot_version(version_parts), snapshot_at=now,
        identity=identity, round=round_out, rules=rules.public_dict(), player_state=player_state,
        timer=timer, question=question, own_outcome=outcome,
        draft=draft_out if device.round_code == "R1B" else None,
        available_actions=actions, score_total=score_total, leaderboard=leaderboard_out,
    )
    await db.commit()
    return response
