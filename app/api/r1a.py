from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from time import perf_counter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..dependencies import require_player
from ..models import (PlayerDevice, Question, QuestionOption, R1AReceipt, Round,
                      RoundStatus, ScoreEvent, Submission)
from ..scoring import r1a_result
from ..timing import elapsed_ms, remaining_ms, utc_now
from ..round_locking import lock_round
from ..player_sessions import authorize_session
from ..r1a_reconciliation import reconcile_r1a
from ..competition_rules import round_rules

router = APIRouter(prefix="/api/r1a", tags=["round-1a"])
class SubmitMCQ(BaseModel):
    option_id: int


def position_at(round_obj: Round, now):
    if round_obj.status != RoundStatus.LIVE.value or not round_obj.question_started_at:
        return None
    rules = round_rules(round_obj).r1a
    if now < round_obj.question_started_at or now >= round_obj.question_started_at + timedelta(milliseconds=rules.question_duration_ms):
        return None
    position = round_obj.current_position or 0
    return position if 1 <= position <= rules.question_count else None


async def live_context(db: AsyncSession, *, locked: bool = False, reconcile: bool = False):
    if locked:
        round_obj = await lock_round(db, "R1A", exclusive=reconcile)
    else:
        round_obj = (await db.execute(select(Round).where(Round.code == "R1A"))).scalar_one_or_none()
    if not round_obj:
        raise HTTPException(409, "Round 1(a) has not been configured")
    now = utc_now()
    if reconcile:
        await reconcile_r1a(db, now=now, locked_round=round_obj)
    position = position_at(round_obj, now)
    return round_obj, now, position


@router.get("/state")
async def state(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj, now, position = await live_context(db, locked=True, reconcile=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1A")
    await db.commit()
    effective_status = round_obj.status
    if round_obj.status == RoundStatus.LIVE.value and position is None:
        effective_status = "ENDED" if round_obj.current_position == round_rules(round_obj).r1a.question_count else "WAITING"
    deadline = (round_obj.question_started_at + timedelta(milliseconds=round_rules(round_obj).r1a.question_duration_ms)) if position else None
    return {
        "rules": round_rules(round_obj).public_dict(),
        "status": effective_status,
        "position": position,
        "last_position": round_obj.current_position,
        "round_started_at": round_obj.started_at,
        "server_now": now,
        "question_deadline": deadline,
        "remaining_ms": remaining_ms(deadline, now) if deadline else 0,
    }


@router.get("/question")
async def question(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj, now, position = await live_context(db, locked=True, reconcile=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1A")
    if position is None:
        await db.commit()
        raise HTTPException(409, "Round 1(a) is not live or has finished")
    q = (await db.execute(select(Question).where(
        Question.round_code == "R1A", Question.position == position, Question.enabled.is_(True)
    ))).scalar_one_or_none()
    if not q:
        raise HTTPException(503, "The current question is not configured")
    options = (await db.execute(select(QuestionOption).where(
        QuestionOption.question_id == q.id
    ).order_by(QuestionOption.position))).scalars().all()
    deadline = round_obj.question_started_at + timedelta(milliseconds=round_rules(round_obj).r1a.question_duration_ms)
    await db.commit()
    return {
        "rules": round_rules(round_obj).public_dict(),
        "position": position,
        "question": {
            "id": q.id, "text": q.question_text, "image_url": q.question_image_url,
            "options": [{"id": o.id, "position": o.position, "text": o.option_text,
                         "image_url": o.image_url} for o in options],
        },
        "server_now": now,
        "question_deadline": deadline,
        "remaining_ms": remaining_ms(deadline, now),
    }


async def resolve_receipt(db: AsyncSession, receipt: R1AReceipt, round_obj: Round):
    """Resolve one durable receipt while the caller owns a Round lock."""
    if receipt.status != "PENDING":
        return receipt
    now = utc_now()
    position = receipt.question_position
    if (position is None or receipt.question_started_at is None
            or receipt.question_deadline is None or receipt.received_at >= receipt.question_deadline):
        receipt.status = "REJECTED"; receipt.rejection_code = "DEADLINE"; receipt.resolved_at = now
        return receipt
    if receipt.session_id:
        valid_session = await db.scalar(select(PlayerDevice.id).where(
            PlayerDevice.team_id == receipt.team_id,
            PlayerDevice.session_id == receipt.session_id,
            PlayerDevice.round_code == "R1A",
            PlayerDevice.created_at <= receipt.received_at,
            PlayerDevice.expires_at > receipt.received_at,
            (PlayerDevice.revoked_at.is_(None) | (PlayerDevice.revoked_at > receipt.received_at)),
        ))
        if valid_session is None:
            receipt.status = "REJECTED"; receipt.rejection_code = "SESSION"; receipt.resolved_at = now
            return receipt
    q = (await db.execute(select(Question).where(
        Question.round_code == "R1A", Question.position == position, Question.enabled.is_(True)
    ))).scalar_one_or_none()
    if not q:
        receipt.status = "REJECTED"; receipt.rejection_code = "QUESTION"; receipt.resolved_at = now
        return receipt
    valid_option = (await db.execute(select(QuestionOption.id).where(
        QuestionOption.id == receipt.option_id, QuestionOption.question_id == q.id
    ))).scalar_one_or_none()
    if valid_option is None:
        receipt.status = "REJECTED"; receipt.rejection_code = "OPTION"; receipt.resolved_at = now
        return receipt

    elapsed = elapsed_ms(receipt.question_started_at, receipt.received_at)
    rules = round_rules(round_obj).r1a
    result = "CORRECT" if receipt.option_id == q.correct_option_id else "WRONG"
    points = r1a_result(result == "CORRECT", elapsed, rules)

    statement = insert(Submission).values(
        team_id=receipt.team_id, round_code="R1A", question_position=position,
        player_number=None, selected_option_id=receipt.option_id, submitted_at=receipt.received_at,
        elapsed_ms=elapsed, result=result, points=points, recorded_at=now,
    ).on_conflict_do_nothing(
        constraint="uq_team_round_question"
    ).returning(Submission.id)
    submission_id = (await db.execute(statement)).scalar_one_or_none()
    if submission_id is None:
        receipt.status = "REJECTED"; receipt.rejection_code = "DUPLICATE"; receipt.resolved_at = now
        return receipt
    db.add(ScoreEvent(team_id=receipt.team_id, round_code="R1A",
                      submission_id=submission_id, reason=f"R1A_Q{position}_{result}", points=points))
    receipt.status = "ACCEPTED"; receipt.submission_id = submission_id; receipt.resolved_at = now
    return receipt


@router.post("/submit")
async def submit(data: SubmitMCQ, user=Depends(require_player), db: AsyncSession = Depends(get_db),
                 request: Request = None):
    timing = request.scope.get("r1a_timing", {}) if request else {}
    received_at = request.scope.get("r1a_received_at") if request else None
    received_at = received_at or utc_now()

    waited = perf_counter()
    await db.connection()
    timing["db_pool_wait_ms"] = (perf_counter() - waited) * 1000
    waited = perf_counter()
    round_obj = await lock_round(db, "R1A", exclusive=False)
    timing["lock_wait_ms"] = (perf_counter() - waited) * 1000
    if not round_obj:
        raise HTTPException(409, "Round 1(a) has not been configured")
    rules = round_rules(round_obj).r1a
    start = round_obj.question_started_at
    deadline = start + timedelta(milliseconds=rules.question_duration_ms) if start else None
    eligible = bool(round_obj.status == RoundStatus.LIVE.value and start and deadline
                    and start <= received_at < deadline)
    receipt = R1AReceipt(
        team_id=user["team_id"], session_id=user.get("sid") or "", option_id=data.option_id,
        received_at=received_at,
        question_position=round_obj.current_position if eligible else None,
        question_started_at=start if eligible else None,
        question_deadline=deadline if eligible else deadline,
        status="PENDING" if eligible else "REJECTED",
        resolved_at=None if eligible else utc_now(),
        rejection_code=None if eligible else "DEADLINE",
    )
    db.add(receipt)
    wrote = perf_counter()
    await db.commit()
    timing["admission_commit_ms"] = (perf_counter() - wrote) * 1000
    if not eligible:
        raise HTTPException(409, "Question time has expired or is not live")

    waited = perf_counter()
    await db.connection()
    timing["db_pool_wait_ms"] += (perf_counter() - waited) * 1000
    waited = perf_counter()
    round_obj = await lock_round(db, "R1A", exclusive=False)
    timing["lock_wait_ms"] += (perf_counter() - waited) * 1000
    receipt = await db.scalar(select(R1AReceipt).where(R1AReceipt.id == receipt.id)
                              .with_for_update().execution_options(populate_existing=True))
    wrote = perf_counter()
    if receipt.status == "PENDING":
        await resolve_receipt(db, receipt, round_obj)
    await db.commit()
    timing["write_commit_ms"] = (perf_counter() - wrote) * 1000
    if receipt.status != "ACCEPTED":
        code = 400 if receipt.rejection_code == "OPTION" else 409
        detail = "The selected option does not belong to this question" if code == 400 else "Already submitted"
        raise HTTPException(code, detail)
    # Acceptance is immediate; correctness and score reveal only at question close.
    submission = await db.get(Submission, receipt.submission_id)
    return {"position": receipt.question_position, "accepted": True, "result": None,
            "points": None, "elapsed_ms": submission.elapsed_ms}
