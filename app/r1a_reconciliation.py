"""Durable, idempotent reconciliation for expired R1A questions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Round, RoundEnrollment, RoundStatus, ScoreEvent, Submission
from .round_locking import lock_round
from .timing import utc_now

QUESTION_SECONDS = 30
QUESTION_COUNT = 23


@dataclass(frozen=True)
class ReconciliationResult:
    position: int | None
    deadline: datetime | None
    inserted: int = 0
    round_ended: bool = False


async def reconcile_r1a(
    db: AsyncSession, *, now: datetime | None = None, locked_round: Round | None = None
) -> ReconciliationResult:
    """Finalize the currently revealed question if its persisted deadline passed.

    The exclusive Round lock serializes submissions, reveal/pause/resume, and other
    reconcilers. Callers may pass a Round row they already locked exclusively.
    """
    round_obj = locked_round or await lock_round(db, "R1A", exclusive=True)
    if not round_obj:
        return ReconciliationResult(None, None)

    authoritative_now = now or utc_now()
    position = round_obj.current_position
    if (round_obj.status != RoundStatus.LIVE.value or not round_obj.question_started_at
            or position is None or not 1 <= position <= QUESTION_COUNT):
        return ReconciliationResult(position, None)

    deadline = round_obj.question_started_at + timedelta(seconds=QUESTION_SECONDS)
    if authoritative_now < deadline:
        return ReconciliationResult(position, deadline)

    # The roster is the start-time snapshot, never current sessions/team status.
    # Explicit literals avoid treating INSERT..SELECT values as column names.
    from sqlalchemy import literal
    statement = insert(Submission).from_select(
        ["team_id", "round_code", "question_position", "player_number",
         "submitted_at", "timed_out_at", "recorded_at", "result", "points"],
        select(
            RoundEnrollment.team_id, literal("R1A"), literal(position),
            literal(None), literal(None), literal(deadline), literal(authoritative_now),
            literal("UNANSWERED"), literal(0),
        ).where(RoundEnrollment.round_code == "R1A")
    ).on_conflict_do_nothing(constraint="uq_team_round_question").returning(
        Submission.id, Submission.team_id
    )
    inserted_rows = (await db.execute(statement)).all()
    for submission_id, team_id in inserted_rows:
        db.add(ScoreEvent(
            team_id=team_id, round_code="R1A", submission_id=submission_id,
            reason=f"R1A_Q{position}_UNANSWERED", points=0,
            created_at=authoritative_now,
        ))

    ended = position == QUESTION_COUNT
    if ended:
        round_obj.status = RoundStatus.ENDED.value
        round_obj.ended_at = deadline

    return ReconciliationResult(position, deadline, len(inserted_rows), ended)
