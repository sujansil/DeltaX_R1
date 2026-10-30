"""Idempotent R1B budget-expiry finalization and periodic reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (Question, R1BDraft, Round, RoundStatus, ScoreEvent,
                     Submission, TeamRoundState)
from .round_locking import lock_round
from .scoring import numeric_correct
from .competition_rules import round_rules
from .timing import elapsed_ms, utc_now


@dataclass(frozen=True)
class FinalizationResult:
    expired: bool
    outcome_created: bool = False
    deadline: datetime | None = None


def effective_remaining_ms(state: TeamRoundState, now: datetime) -> int:
    remaining = max(0, state.remaining_time_ms)
    if state.clock_running_since is not None:
        remaining = max(0, remaining - elapsed_ms(state.clock_running_since, now))
    return remaining


def expiry_deadline(state: TeamRoundState) -> datetime | None:
    if state.clock_running_since is None:
        return None
    return state.team_deadline or (
        state.clock_running_since + timedelta(milliseconds=state.remaining_time_ms)
    )


async def finalize_expired_team(
    db: AsyncSession, round_obj: Round, state: TeamRoundState, now: datetime | None = None
) -> FinalizationResult:
    """Finalize one locked team state after authoritative round/state checks."""
    processing_time = now or utc_now()
    if round_obj.status != RoundStatus.LIVE.value:
        return FinalizationResult(False)
    if state.turn_status in ("FINISHED", "EXPIRED") or state.clock_started_at is None:
        return FinalizationResult(False)
    deadline = expiry_deadline(state)
    if deadline is None or processing_time < deadline or effective_remaining_ms(state, processing_time) > 0:
        return FinalizationResult(False, deadline=deadline)

    outcome_created = False
    if state.turn_status == "ACTIVE":
        position = state.current_question_position
        question = await db.scalar(select(Question).where(
            Question.round_code == "R1B", Question.position == position,
            Question.enabled.is_(True),
        ))
        if not question:
            # Configuration loss must be visible and retried, not silently finalize.
            raise RuntimeError(f"R1B question {position} is not configured")
        draft = await db.scalar(select(R1BDraft).where(
            R1BDraft.team_id == state.team_id,
            R1BDraft.turn_id == state.current_turn_id,
            R1BDraft.question_position == position,
            R1BDraft.player_number == state.current_player,
            R1BDraft.accepted_at <= deadline,
        ))
        answer = draft.answer_text if draft else ""
        if not answer.strip():
            result, points, source = "UNANSWERED", 0, "TIMEOUT_EMPTY"
        else:
            correct = numeric_correct(answer, question.correct_numeric,
                                      question.tolerance_abs, question.tolerance_rel)
            rules = round_rules(round_obj).r1b
            result, points = (("CORRECT", rules.correct_score) if correct else ("WRONG", rules.wrong_score))
            source = "TIMEOUT_DRAFT"
        submission_id = await db.scalar(insert(Submission).values(
            team_id=state.team_id, round_code="R1B", question_position=position,
            player_number=state.current_player, answer_text=answer,
            submitted_at=None, timed_out_at=deadline, recorded_at=processing_time,
            elapsed_ms=elapsed_ms(state.ready_at, deadline), result=result, points=points,
            finalization_source=source, draft_id=draft.id if draft else None,
            draft_version=draft.version if draft else None,
        ).on_conflict_do_nothing(constraint="uq_team_round_question").returning(Submission.id))
        if submission_id is not None:
            db.add(ScoreEvent(
                team_id=state.team_id, round_code="R1B", submission_id=submission_id,
                reason=f"R1B_Q{position}_{result}", points=points,
                created_at=processing_time,
            ))
            outcome_created = True
        source_for_state = source
    else:
        # CONTINUOUS can expire between turns. The next question was never revealed.
        source_for_state = "TIMEOUT_HANDOVER"

    state.remaining_time_ms = 0
    state.clock_running_since = None
    state.team_deadline = None
    state.turn_status = "EXPIRED"
    state.expired_at = deadline
    state.finalized_at = processing_time
    state.finalization_source = source_for_state
    state.finished_at = processing_time
    return FinalizationResult(True, outcome_created, deadline)


async def reconcile_r1b_batch(db: AsyncSession, *, batch_size: int = 100) -> int:
    """Finalize one bounded batch. Safe across processes via SKIP LOCKED."""
    round_obj = await lock_round(db, "R1B", exclusive=False)
    if not round_obj or round_obj.status != RoundStatus.LIVE.value:
        return 0
    now = utc_now()  # sampled after the authoritative round lock
    states = (await db.scalars(select(TeamRoundState).where(
        TeamRoundState.round_code == "R1B",
        TeamRoundState.turn_status.in_(("ACTIVE", "WAITING")),
        TeamRoundState.clock_running_since.is_not(None),
        TeamRoundState.team_deadline.is_not(None),
        TeamRoundState.team_deadline <= now,
    ).order_by(TeamRoundState.team_deadline, TeamRoundState.id)
      .limit(batch_size).with_for_update(skip_locked=True))).all()
    finalized = 0
    for state in states:
        if (await finalize_expired_team(db, round_obj, state, now)).expired:
            finalized += 1
    return finalized
