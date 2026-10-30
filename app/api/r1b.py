from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..dependencies import require_player
from ..models import (Question, R1BDraft, R1BTimerMode, Round, RoundStatus,
                      ScoreEvent, Submission, TeamRoundState)
from ..scoring import numeric_correct
from ..timing import elapsed_ms, utc_now
from ..round_locking import lock_round
from ..player_sessions import authorize_session
from ..r1b_finalization import effective_remaining_ms, finalize_expired_team
from ..competition_rules import round_rules

router = APIRouter(prefix="/api/r1b", tags=["round-1b"])
class SubmitNumeric(BaseModel):
    answer: str | None = None
    skip: bool = False


class SaveDraft(BaseModel):
    turn_id: str = Field(min_length=36, max_length=36)
    version: int = Field(ge=1)
    answer: str = Field(max_length=500)


async def require_live_round(db: AsyncSession, allow_paused: bool = False, *, locked: bool = False):
    if locked:
        round_obj = await lock_round(db, "R1B", exclusive=False)
    else:
        round_obj = (await db.execute(select(Round).where(Round.code == "R1B"))).scalar_one_or_none()
    allowed = (RoundStatus.LIVE.value, RoundStatus.PAUSED.value) if allow_paused else (RoundStatus.LIVE.value,)
    if not round_obj or round_obj.status not in allowed:
        raise HTTPException(409, "Round 1(b) is not live")
    return round_obj


async def locked_state(db: AsyncSession, team_id: int, round_obj: Round):
    budget_ms = round_rules(round_obj).r1b.total_answering_budget_ms
    await db.execute(insert(TeamRoundState).values(
        team_id=team_id, round_code="R1B", current_player=1,
        current_question_position=1, turn_status="NOT_STARTED",
        remaining_time_ms=budget_ms,
    ).on_conflict_do_nothing(constraint="uq_team_round"))
    return (await db.execute(select(TeamRoundState).where(
        TeamRoundState.team_id == team_id, TeamRoundState.round_code == "R1B"
    ).with_for_update())).scalar_one()


def freeze_clock(state: TeamRoundState, now) -> int:
    remaining = effective_remaining_ms(state, now)
    state.remaining_time_ms = remaining
    state.clock_running_since = None
    state.team_deadline = None
    return remaining


def run_clock(state: TeamRoundState, now) -> None:
    state.clock_running_since = now
    state.team_deadline = now + timedelta(milliseconds=state.remaining_time_ms)


def clock_reference(round_obj: Round, now):
    if (round_obj.status == RoundStatus.PAUSED.value and round_obj.paused_at
            and round_obj.paused_at < now):
        return round_obj.paused_at
    return now


def serialize_state(state: TeamRoundState, round_obj: Round, now):
    reference = clock_reference(round_obj, now)
    remaining = effective_remaining_ms(state, reference)
    running = (
        round_obj.status == RoundStatus.LIVE.value
        and state.clock_running_since is not None
        and state.turn_status not in ("FINISHED", "EXPIRED")
    )
    return {
        "rules": round_rules(round_obj).public_dict(),
        "round_status": round_obj.status,
        "timer_mode": round_obj.r1b_timer_mode,
        "current_player": state.current_player,
        "current_question_position": state.current_question_position,
        "turn_status": state.turn_status,
        "team_started_at": state.team_started_at,
        "clock_started_at": state.clock_started_at,
        "clock_running": running,
        "team_deadline": state.team_deadline if running else None,
        "server_now": now,
        "remaining_ms": remaining,
    }


def require_turn_slot(user, state: TeamRoundState):
    if user.get("slot") is not None and user["slot"] != state.current_player:
        raise HTTPException(403, f"Relay slot {state.current_player} owns the active turn")


@router.get("/state")
async def state(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj = await require_live_round(db, allow_paused=True, locked=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1B")
    async with db.begin_nested():
        team_state = await locked_state(db, user["team_id"], round_obj)
        now = utc_now()
        await finalize_expired_team(db, round_obj, team_state, now)
    await db.commit()
    return serialize_state(team_state, round_obj, now)


@router.post("/start")
async def start_team(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj = await require_live_round(db, locked=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1B", lock=True)
    async with db.begin_nested():
        team_state = await locked_state(db, user["team_id"], round_obj)
        require_turn_slot(user, team_state)
        if team_state.team_started_at:
            response = {"ok": True, "already_started": True}
        else:
            now = utc_now()
            team_state.team_started_at = now
            team_state.remaining_time_ms = round_rules(round_obj).r1b.total_answering_budget_ms
            team_state.clock_started_at = None
            team_state.clock_running_since = None
            team_state.team_deadline = None
            team_state.current_player = 1
            team_state.current_question_position = 1
            team_state.turn_status = "WAITING"
            team_state.current_turn_id = None
            response = {"ok": True, "already_started": False}
    await db.commit()
    return {**response, **serialize_state(team_state, round_obj, utc_now())}


@router.post("/ready")
async def ready(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj = await require_live_round(db, locked=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1B", lock=True)
    expired = False
    async with db.begin_nested():
        team_state = await locked_state(db, user["team_id"], round_obj)
        require_turn_slot(user, team_state)
        now = utc_now()
        if team_state.turn_status != "WAITING":
            raise HTTPException(409, "The team is not waiting for READY")
        if (await finalize_expired_team(db, round_obj, team_state, now)).expired:
            expired = True
        else:
            if team_state.clock_started_at is None:
                team_state.clock_started_at = now
            # CONTINUOUS handovers are already running; do not reset their anchor.
            if team_state.clock_running_since is None:
                run_clock(team_state, now)
            team_state.ready_at = now
            team_state.turn_status = "ACTIVE"
            team_state.current_turn_id = str(uuid4())
    await db.commit()
    if expired:
        raise HTTPException(409, "Team time has expired")
    return {"ok": True, **serialize_state(team_state, round_obj, utc_now())}


@router.get("/question")
async def question(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj = await require_live_round(db, locked=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1B", lock=True)
    team_state = (await db.execute(select(TeamRoundState).where(
        TeamRoundState.team_id == user["team_id"], TeamRoundState.round_code == "R1B"
    ).with_for_update())).scalar_one_or_none()
    now = utc_now()
    if not team_state or team_state.turn_status != "ACTIVE":
        raise HTTPException(409, "There is no active relay question")
    require_turn_slot(user, team_state)
    if (await finalize_expired_team(db, round_obj, team_state, now)).expired:
        await db.commit()
        raise HTTPException(409, "Team time has expired")
    q = (await db.execute(select(Question).where(
        Question.round_code == "R1B",
        Question.position == team_state.current_question_position,
        Question.enabled.is_(True),
    ))).scalar_one_or_none()
    if not q:
        raise HTTPException(503, "The current question is not configured")
    return {
        "position": q.position,
        "player_number": team_state.current_player,
        "question": {"id": q.id, "text": q.question_text, "image_url": q.question_image_url},
        "timer_mode": round_obj.r1b_timer_mode,
        "clock_running": True,
        "team_deadline": team_state.team_deadline,
        "server_now": now,
        "remaining_ms": effective_remaining_ms(team_state, now),
        "turn_id": team_state.current_turn_id,
    }


@router.get("/draft")
async def get_draft(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj = await require_live_round(db, allow_paused=True, locked=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1B", lock=True)
    team_state = await locked_state(db, user["team_id"], round_obj)
    require_turn_slot(user, team_state)
    now = utc_now()
    await finalize_expired_team(db, round_obj, team_state, now)
    if team_state.turn_status != "ACTIVE" or not team_state.current_turn_id:
        await db.commit()
        raise HTTPException(409, "There is no active relay draft")
    draft = await db.scalar(select(R1BDraft).where(
        R1BDraft.team_id == user["team_id"],
        R1BDraft.turn_id == team_state.current_turn_id,
    ))
    response = {
        "turn_id": team_state.current_turn_id,
        "position": team_state.current_question_position,
        "player_number": team_state.current_player,
        "answer": draft.answer_text if draft else "",
        "version": draft.version if draft else 0,
        "accepted_at": draft.accepted_at if draft else None,
    }
    await db.commit()
    return response


@router.put("/draft")
async def save_draft(data: SaveDraft, user=Depends(require_player),
                     db: AsyncSession = Depends(get_db)):
    round_obj = await require_live_round(db, locked=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1B", lock=True)
    team_state = await locked_state(db, user["team_id"], round_obj)
    require_turn_slot(user, team_state)
    now = utc_now()
    if (await finalize_expired_team(db, round_obj, team_state, now)).expired:
        await db.commit()
        raise HTTPException(409, "Team time has expired")
    if team_state.turn_status != "ACTIVE" or data.turn_id != team_state.current_turn_id:
        raise HTTPException(409, "Draft belongs to a stale relay turn")
    draft = await db.scalar(select(R1BDraft).where(
        R1BDraft.team_id == user["team_id"], R1BDraft.turn_id == data.turn_id,
    ).with_for_update())
    if draft and data.version <= draft.version:
        raise HTTPException(409, "Draft version is stale")
    if draft is None:
        draft = R1BDraft(
            team_id=user["team_id"], question_position=team_state.current_question_position,
            player_number=team_state.current_player, turn_id=data.turn_id,
            version=data.version, answer_text=data.answer, accepted_at=now,
        )
        db.add(draft)
    else:
        draft.version = data.version
        draft.answer_text = data.answer
        draft.accepted_at = now
    await db.commit()
    return {"turn_id": data.turn_id, "version": data.version,
            "answer": data.answer, "accepted_at": now}


@router.post("/submit")
async def submit(data: SubmitNumeric, user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj = await require_live_round(db, locked=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1B", lock=True)
    expired = False
    response = None
    async with db.begin_nested():
        team_state = await locked_state(db, user["team_id"], round_obj)
        require_turn_slot(user, team_state)
        now = utc_now()
        if team_state.turn_status != "ACTIVE":
            raise HTTPException(409, "The team does not have an active turn")
        if (await finalize_expired_team(db, round_obj, team_state, now)).expired:
            expired = True
        else:
            remaining = freeze_clock(team_state, now)
            position = team_state.current_question_position
            q = (await db.execute(select(Question).where(
                Question.round_code == "R1B", Question.position == position,
                Question.enabled.is_(True),
            ))).scalar_one_or_none()
            if not q:
                raise HTTPException(503, "The current question is not configured")

            if data.skip:
                result, points = "SKIPPED", round_rules(round_obj).r1b.skip_unanswered_score
            else:
                correct = numeric_correct(data.answer, q.correct_numeric,
                                          q.tolerance_abs, q.tolerance_rel)
                rules = round_rules(round_obj).r1b
                result, points = (("CORRECT", rules.correct_score)
                                  if correct else ("WRONG", rules.wrong_score))
            submission_id = (await db.execute(insert(Submission).values(
                team_id=user["team_id"], round_code="R1B", question_position=position,
                player_number=team_state.current_player, answer_text=data.answer,
                submitted_at=now, recorded_at=now,
                elapsed_ms=elapsed_ms(team_state.ready_at, now),
                result=result, points=points,
                finalization_source="SKIP" if data.skip else "MANUAL",
            ).on_conflict_do_nothing(
                constraint="uq_team_round_question"
            ).returning(Submission.id))).scalar_one_or_none()
            if submission_id is None:
                raise HTTPException(409, "Turn already finished")
            db.add(ScoreEvent(team_id=user["team_id"], round_code="R1B",
                              submission_id=submission_id,
                              reason=f"R1B_Q{position}_{result}", points=points))

            if position >= round_rules(round_obj).r1b.turn_count:
                team_state.turn_status = "FINISHED"
                team_state.finished_at = now
                next_player = None
            else:
                team_state.current_player += 1
                team_state.current_question_position += 1
                team_state.turn_status = "WAITING"
                team_state.ready_at = None
                if round_obj.r1b_timer_mode == R1BTimerMode.CONTINUOUS.value:
                    # Normalize at the submit instant, then continue through handover.
                    team_state.remaining_time_ms = remaining
                    run_clock(team_state, now)
                next_player = team_state.current_player
            team_state.current_turn_id = None
            response = {"position": position, "result": result,
                        "points": points, "next_player": next_player}
    await db.commit()
    if expired:
        raise HTTPException(409, "Team time has expired; late answers are not accepted")
    return response
