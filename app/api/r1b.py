from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from time import perf_counter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..dependencies import require_player
from ..models import (Question, R1BDraft, R1BReceipt, R1BTimerMode, Round, RoundStatus,
                      ScoreEvent, Submission, TeamRoundState)
from ..scoring import numeric_correct
from ..timing import elapsed_ms, utc_now
from ..round_locking import lock_round
from ..player_sessions import authorize_session
from ..r1b_finalization import effective_remaining_ms, expiry_deadline, finalize_expired_team
from ..competition_rules import round_rules

router = APIRouter(prefix="/api/r1b", tags=["round-1b"])
class SubmitNumeric(BaseModel):
    # Optional only for backwards-compatible internal callers; an absent value
    # never matches an active turn and is rejected by the endpoint.
    turn_id: str | None = Field(default=None, min_length=36, max_length=36)
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
    ).with_for_update().execution_options(populate_existing=True))).scalar_one()


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


async def resolve_receipt(db: AsyncSession, receipt: R1BReceipt, round_obj: Round,
                          team_state: TeamRoundState):
    """Resolve a registered receipt while the caller owns the team-state lock."""
    if receipt.status != "PENDING":
        return receipt
    now = utc_now()
    if (team_state.turn_status != "ACTIVE"
            or team_state.current_turn_id != receipt.turn_id
            or team_state.current_question_position != receipt.question_position
            or team_state.current_player != receipt.player_number):
        receipt.status = "REJECTED"; receipt.rejection_code = "STALE_TURN"; receipt.resolved_at = now
        return receipt
    if receipt.received_at >= receipt.team_deadline:
        receipt.status = "REJECTED"; receipt.rejection_code = "DEADLINE"; receipt.resolved_at = now
        return receipt
    q = await db.scalar(select(Question).where(
        Question.round_code == "R1B", Question.position == receipt.question_position,
        Question.enabled.is_(True)))
    if not q:
        receipt.status = "REJECTED"; receipt.rejection_code = "QUESTION"; receipt.resolved_at = now
        return receipt
    rules = round_rules(round_obj).r1b
    if receipt.action == "SKIP":
        result, points = "SKIPPED", rules.skip_unanswered_score
    else:
        correct = numeric_correct(receipt.answer_text, q.correct_numeric, q.tolerance_abs, q.tolerance_rel)
        result, points = (("CORRECT", rules.correct_score) if correct else ("WRONG", rules.wrong_score))
    submission_id = await db.scalar(insert(Submission).values(
        team_id=receipt.team_id, round_code="R1B", question_position=receipt.question_position,
        player_number=receipt.player_number, answer_text=receipt.answer_text,
        submitted_at=receipt.received_at, recorded_at=now,
        elapsed_ms=elapsed_ms(receipt.turn_ready_at, receipt.received_at),
        result=result, points=points,
        finalization_source="SKIP" if receipt.action == "SKIP" else "MANUAL",
    ).on_conflict_do_nothing(constraint="uq_team_round_question").returning(Submission.id))
    if submission_id is None:
        receipt.status = "REJECTED"; receipt.rejection_code = "DUPLICATE"; receipt.resolved_at = now
        return receipt
    db.add(ScoreEvent(team_id=receipt.team_id, round_code="R1B", submission_id=submission_id,
                      reason=f"R1B_Q{receipt.question_position}_{result}", points=points))
    if rules.show_results_after_each_question:
        from ..question_reveal import R1B_TURN_KIND
        from ..services import LeaderboardService
        await LeaderboardService().publish(
            db, "R1B", kind=R1B_TURN_KIND,
            metadata={"team_id": receipt.team_id, "turn_id": receipt.turn_id,
                      "question_position": receipt.question_position})
    remaining = max(0, int((receipt.team_deadline - receipt.received_at).total_seconds() * 1000))
    team_state.remaining_time_ms = remaining
    team_state.clock_running_since = None
    team_state.team_deadline = None
    if receipt.question_position >= rules.turn_count:
        team_state.turn_status = "FINISHED"; team_state.finished_at = now
    else:
        team_state.current_player += 1
        team_state.current_question_position += 1
        team_state.turn_status = "WAITING"
        team_state.ready_at = None
        if round_obj.r1b_timer_mode == R1BTimerMode.CONTINUOUS.value:
            run_clock(team_state, receipt.received_at)
    team_state.current_turn_id = None
    receipt.status = "ACCEPTED"; receipt.submission_id = submission_id; receipt.resolved_at = now
    return receipt


@router.post("/submit")
async def submit(data: SubmitNumeric, user=Depends(require_player), db: AsyncSession = Depends(get_db),
                 request: Request = None):
    if not data.turn_id:
        raise HTTPException(409, "An immutable relay turn ID is required")
    timing = request.scope.get("r1b_timing", {}) if request else {}
    received_at = (request.scope.get("r1b_received_at") if request else None) or utc_now()
    waited = perf_counter(); await db.connection()
    timing["db_pool_wait_ms"] = (perf_counter() - waited) * 1000
    waited = perf_counter()
    round_obj = await require_live_round(db, locked=True)
    timing["lock_wait_ms"] = (perf_counter() - waited) * 1000
    if user.get("sid"):
        await authorize_session(db, user, "R1B", lock=True)
    team_state = await locked_state(db, user["team_id"], round_obj)
    deadline = expiry_deadline(team_state)
    eligible = bool(team_state.turn_status == "ACTIVE" and team_state.current_turn_id == data.turn_id
                    and team_state.ready_at and deadline and received_at < deadline)
    receipt = R1BReceipt(
        team_id=user["team_id"], session_id=user.get("sid") or "", turn_id=data.turn_id,
        question_position=team_state.current_question_position,
        player_number=team_state.current_player, action="SKIP" if data.skip else "SUBMIT",
        answer_text=data.answer, received_at=received_at,
        turn_ready_at=team_state.ready_at or received_at,
        team_deadline=deadline or received_at,
        status="PENDING" if eligible else "REJECTED", resolved_at=None if eligible else utc_now(),
        rejection_code=None if eligible else ("STALE_TURN" if team_state.current_turn_id != data.turn_id else "DEADLINE"),
    )
    db.add(receipt); wrote = perf_counter(); await db.commit()
    timing["admission_commit_ms"] = (perf_counter() - wrote) * 1000
    if not eligible:
        raise HTTPException(409, "Turn is stale, closed, or past its deadline")
    waited = perf_counter(); await db.connection()
    timing["db_pool_wait_ms"] += (perf_counter() - waited) * 1000
    round_obj = await require_live_round(db, locked=True)
    team_state = await locked_state(db, user["team_id"], round_obj)
    receipt = await db.scalar(select(R1BReceipt).where(R1BReceipt.id == receipt.id)
                              .with_for_update().execution_options(populate_existing=True))
    if receipt is None:
        # An authorized rehearsal reset may have invalidated the admitted turn
        # while this request was between its two transactions.
        raise HTTPException(409, {"code": "SUBMISSION_STATE_CHANGED",
            "message": "Round state changed while recording this answer; refresh and sign in again"})
    await resolve_receipt(db, receipt, round_obj, team_state)
    wrote = perf_counter()
    await db.commit()
    timing["write_commit_ms"] = (perf_counter() - wrote) * 1000
    if receipt.status != "ACCEPTED":
        raise HTTPException(409, "Turn already finished")
    response = {"position": receipt.question_position, "accepted": True,
                "message": "Answer recorded",
                "next_player": team_state.current_player if team_state.turn_status == "WAITING" else None}
    if round_rules(round_obj).r1b.show_results_after_each_question:
        from ..question_reveal import read_leaderboard
        board = await read_leaderboard(db, "R1B", user["team_id"], include_intermediate=True)
        submission = await db.get(Submission, receipt.submission_id)
        if submission is None:
            raise HTTPException(409, {"code": "SUBMISSION_STATE_CHANGED",
                "message": "Round state changed while recording this answer; refresh and sign in again"})
        response.update({"result": submission.result, "points": submission.points,
                         "score_total": board["mine"]["score"] if board.get("mine") else None,
                         "rank": board["mine"]["rank"] if board.get("mine") else None,
                         "provisional": True,
                         "publication_id": board.get("publication_id")})
    return response
