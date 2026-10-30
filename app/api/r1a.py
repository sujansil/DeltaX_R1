from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..dependencies import require_player
from ..models import Question, QuestionOption, Round, RoundStatus, ScoreEvent, Submission
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


@router.post("/submit")
async def submit(data: SubmitMCQ, user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    # The exclusive lock lets this request reconcile the deadline before writing.
    round_obj, now, position = await live_context(db, locked=True, reconcile=True)
    if user.get("sid"):
        await authorize_session(db, user, "R1A", lock=True)
    if position is None:
        await db.commit()
        raise HTTPException(409, "Round 1(a) is not live or has finished")
    q = (await db.execute(select(Question).where(
        Question.round_code == "R1A", Question.position == position, Question.enabled.is_(True)
    ))).scalar_one_or_none()
    if not q:
        raise HTTPException(503, "The current question is not configured")
    valid_option = (await db.execute(select(QuestionOption.id).where(
        QuestionOption.id == data.option_id, QuestionOption.question_id == q.id
    ))).scalar_one_or_none()
    if valid_option is None:
        raise HTTPException(400, "The selected option does not belong to this question")

    question_start = round_obj.question_started_at
    elapsed = elapsed_ms(question_start, now)
    rules = round_rules(round_obj).r1a
    if elapsed >= rules.question_duration_ms:
        raise HTTPException(409, "Question time has expired")
    result = "CORRECT" if data.option_id == q.correct_option_id else "WRONG"
    points = r1a_result(result == "CORRECT", elapsed, rules)

    statement = insert(Submission).values(
        team_id=user["team_id"], round_code="R1A", question_position=position,
        player_number=None, selected_option_id=data.option_id, submitted_at=now,
        elapsed_ms=elapsed, result=result, points=points, recorded_at=now,
    ).on_conflict_do_nothing(
        constraint="uq_team_round_question"
    ).returning(Submission.id)
    submission_id = (await db.execute(statement)).scalar_one_or_none()
    if submission_id is None:
        await db.rollback()
        raise HTTPException(409, "Already submitted")
    db.add(ScoreEvent(team_id=user["team_id"], round_code="R1A",
                      submission_id=submission_id, reason=f"R1A_Q{position}_{result}", points=points))
    await db.commit()
    # Acceptance is immediate; correctness and score reveal only at question close.
    return {"position": position, "accepted": True, "result": None, "points": None, "elapsed_ms": elapsed}
