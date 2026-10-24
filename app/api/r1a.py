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

router = APIRouter(prefix="/api/r1a", tags=["round-1a"])
QUESTION_SECONDS = 30
QUESTION_COUNT = 23


class SubmitMCQ(BaseModel):
    option_id: int


def position_at(round_obj: Round, now):
    if round_obj.status != RoundStatus.LIVE.value or not round_obj.question_started_at:
        return None
    if now < round_obj.question_started_at or now >= round_obj.question_started_at + timedelta(seconds=QUESTION_SECONDS):
        return None
    position = round_obj.current_position or 0
    return position if 1 <= position <= QUESTION_COUNT else None


async def live_context(db: AsyncSession, *, locked: bool = False):
    if locked:
        round_obj = await lock_round(db, "R1A", exclusive=False)
    else:
        round_obj = (await db.execute(select(Round).where(Round.code == "R1A"))).scalar_one_or_none()
    if not round_obj:
        raise HTTPException(409, "Round 1(a) has not been configured")
    now = utc_now()
    position = position_at(round_obj, now)
    return round_obj, now, position


@router.get("/state")
async def state(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    round_obj, now, position = await live_context(db)
    effective_status = round_obj.status
    if round_obj.status == RoundStatus.LIVE.value and position is None:
        effective_status = "ENDED" if round_obj.current_position == QUESTION_COUNT else "WAITING"
    deadline = (round_obj.question_started_at + timedelta(seconds=QUESTION_SECONDS)) if position else None
    return {
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
    round_obj, now, position = await live_context(db)
    if position is None:
        raise HTTPException(409, "Round 1(a) is not live or has finished")
    q = (await db.execute(select(Question).where(
        Question.round_code == "R1A", Question.position == position, Question.enabled.is_(True)
    ))).scalar_one_or_none()
    if not q:
        raise HTTPException(503, "The current question is not configured")
    options = (await db.execute(select(QuestionOption).where(
        QuestionOption.question_id == q.id
    ).order_by(QuestionOption.position))).scalars().all()
    deadline = round_obj.question_started_at + timedelta(seconds=QUESTION_SECONDS)
    return {
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
    # The shared round lock coordinates this entire write with pause/end/reveal.
    round_obj, now, position = await live_context(db, locked=True)
    if position is None:
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
    if elapsed >= QUESTION_SECONDS * 1000:
        raise HTTPException(409, "Question time has expired")
    result = "CORRECT" if data.option_id == q.correct_option_id else "WRONG"
    points = r1a_result(result == "CORRECT", elapsed)

    statement = insert(Submission).values(
        team_id=user["team_id"], round_code="R1A", question_position=position,
        player_number=None, selected_option_id=data.option_id, submitted_at=now,
        elapsed_ms=elapsed, result=result, points=points,
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
    return {"position": position, "result": result, "points": points, "elapsed_ms": elapsed}
