"""Durable, idempotent reconciliation for expired R1A questions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (AdminAction, Question, R1AReceipt, Round, RoundEnrollment,
                     RoundStatus, ScoreEvent, Submission)
from .round_locking import lock_round
from .timing import utc_now
from .question_reveal import ensure_question_snapshot
from .competition_rules import round_rules


@dataclass(frozen=True)
class ReconciliationResult:
    position: int | None
    deadline: datetime | None
    inserted: int = 0
    round_ended: bool = False
    advanced: bool = False


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
            or position is None or not 1 <= position <= round_rules(round_obj).r1a.question_count):
        if round_obj.status in ("ENDED", "PAUSED"):
            await ensure_question_snapshot(db, round_obj, authoritative_now)
        return ReconciliationResult(position, None)

    rules = round_rules(round_obj).r1a
    deadline = round_obj.question_started_at + timedelta(milliseconds=rules.question_duration_ms)
    # Admission commits a durable receipt under a shared Round lock. Owning the
    # exclusive lock here therefore fences new admissions while every eligible
    # pending receipt is resolved before unanswered rows or a snapshot exist.
    pending = (await db.scalars(select(R1AReceipt).where(
        R1AReceipt.status == "PENDING",
        R1AReceipt.question_position == position,
    ).order_by(R1AReceipt.id).with_for_update())).all()
    if pending:
        # Local import avoids an import cycle: the HTTP module also invokes this
        # reconciler for read-side expiry checks.
        from .api.r1a import resolve_receipt
        for receipt in pending:
            await resolve_receipt(db, receipt, round_obj)
    if authoritative_now < deadline:
        return ReconciliationResult(position, deadline)

    # Once publication has established the result interval, a later pass owns
    # only the single next reveal. The new question always gets a fresh window.
    if rules.advance_mode == "AUTO" and round_obj.r1a_result_due_at is not None:
        if authoritative_now < round_obj.r1a_result_due_at:
            return ReconciliationResult(position, deadline)
        next_position = position + 1
        question = await db.scalar(select(Question).where(
            Question.round_code == "R1A", Question.position == next_position,
            Question.enabled.is_(True)))
        if not question or question.correct_option_id is None:
            raise RuntimeError(f"R1A question {next_position} is not configured")
        round_obj.current_position = next_position
        round_obj.question_started_at = authoritative_now
        round_obj.r1a_result_due_at = None
        db.add(AdminAction(admin_id=None, action="AUTO_REVEAL_R1A_QUESTION",
                           details=f"position={next_position};source=SYSTEM"))
        return ReconciliationResult(next_position, None, advanced=True)

    # The roster is the start-time snapshot, never current sessions/team status.
    # Explicit literals avoid treating INSERT..SELECT values as column names.
    from sqlalchemy import literal
    statement = insert(Submission).from_select(
        ["team_id", "round_code", "question_position", "player_number",
         "submitted_at", "timed_out_at", "recorded_at", "result", "points"],
        select(
            RoundEnrollment.team_id, literal("R1A"), literal(position),
            literal(None), literal(None), literal(deadline), literal(authoritative_now),
            literal("UNANSWERED"), literal(rules.unanswered_score),
        ).where(RoundEnrollment.round_code == "R1A")
    ).on_conflict_do_nothing(constraint="uq_team_round_question").returning(
        Submission.id, Submission.team_id
    )
    inserted_rows = (await db.execute(statement)).all()
    for submission_id, team_id in inserted_rows:
        db.add(ScoreEvent(
            team_id=team_id, round_code="R1A", submission_id=submission_id,
            reason=f"R1A_Q{position}_UNANSWERED", points=rules.unanswered_score,
            created_at=authoritative_now,
        ))

    # Flush timeout score events before calculating the closed-question board.
    snapshot = await ensure_question_snapshot(db, round_obj, authoritative_now)

    ended = position == rules.question_count
    if ended:
        round_obj.status = RoundStatus.ENDED.value
        round_obj.ended_at = deadline

    elif rules.advance_mode == "AUTO" and snapshot is not None:
        # Anchor to finalized publication time, not the question deadline.
        round_obj.r1a_result_due_at = authoritative_now + timedelta(
            milliseconds=rules.auto_advance_delay_ms)
        if rules.auto_advance_delay_ms == 0:
            next_position = position + 1
            question = await db.scalar(select(Question).where(
                Question.round_code == "R1A", Question.position == next_position,
                Question.enabled.is_(True)))
            if not question or question.correct_option_id is None:
                raise RuntimeError(f"R1A question {next_position} is not configured")
            round_obj.current_position = next_position
            round_obj.question_started_at = authoritative_now
            round_obj.r1a_result_due_at = None
            db.add(AdminAction(admin_id=None, action="AUTO_REVEAL_R1A_QUESTION",
                               details=f"position={next_position};source=SYSTEM"))
            return ReconciliationResult(next_position, None, len(inserted_rows), advanced=True)

    return ReconciliationResult(position, deadline, len(inserted_rows), ended)
