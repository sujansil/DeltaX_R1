import asyncio
import os
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from tests.db_guard import require_disposable_test_database


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]


async def reset_data():
    from app.db import SessionLocal
    from app.models import (AdminAction, LeaderboardSnapshot, PlayerDevice, Question,
                            QuestionOption, R1BDraft, Round, RoundEnrollment,
                            ScoreEvent, Submission, Team, TeamRoundState)
    async with SessionLocal() as db:
        for model in (AdminAction, LeaderboardSnapshot, ScoreEvent, Submission,
                      R1BDraft, PlayerDevice, TeamRoundState, RoundEnrollment,
                      QuestionOption, Question, Round, Team):
            await db.execute(delete(model))
        await db.commit()


async def seed_active(*, mode="CONTINUOUS", remaining_ms=60_000):
    from app.db import SessionLocal
    from app.models import Question, Round, Team, TeamRoundState
    from app.timing import utc_now
    async with SessionLocal() as db:
        team = Team(code="DRAFT", name="Draft", login_username="draft")
        other = Team(code="OTHER", name="Other", login_username="other")
        db.add_all((team, other)); await db.flush()
        q = Question(round_code="R1B", position=1, answer_type="NUMERIC",
                     correct_numeric=Decimal("42"), tolerance_abs=Decimal("0"),
                     tolerance_rel=Decimal("0"))
        db.add(q)
        now = utc_now(); turn_id = str(uuid4())
        db.add(Round(code="R1B", status="LIVE", started_at=now, r1b_timer_mode=mode))
        db.add(TeamRoundState(
            team_id=team.id, round_code="R1B", current_player=1,
            current_question_position=1, turn_status="ACTIVE",
            team_started_at=now, remaining_time_ms=remaining_ms,
            clock_started_at=now, clock_running_since=now,
            team_deadline=now + timedelta(milliseconds=remaining_ms),
            ready_at=now, current_turn_id=turn_id,
        ))
        await db.commit()
        return team.id, other.id, turn_id, now


async def save(team_id, turn_id, version, answer, *, slot=1):
    from app.api import r1b
    from app.db import SessionLocal
    async with SessionLocal() as db:
        return await r1b.save_draft(
            r1b.SaveDraft(turn_id=turn_id, version=version, answer=answer),
            {"team_id": team_id, "slot": slot}, db,
        )


async def expire_with_worker(processing_time):
    from app.db import SessionLocal
    from app.models import TeamRoundState
    from app.r1b_finalization import reconcile_r1b_batch
    async with SessionLocal() as db:
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.round_code == "R1B"))
        state.remaining_time_ms = 1
        state.clock_running_since = processing_time - timedelta(milliseconds=2)
        state.team_deadline = processing_time - timedelta(milliseconds=1)
        await db.commit()
    async with SessionLocal() as db:
        count = await reconcile_r1b_batch(db)
        await db.commit()
        return count


@pytest.mark.parametrize(
    ("answer", "result", "points"),
    [("42", "CORRECT", 4000), ("41", "WRONG", -1500),
     ("", "UNANSWERED", 0), ("1e", "WRONG", -1500)],
)
async def test_latest_draft_finalized_at_expiry(monkeypatch, answer, result, points):
    require_disposable_test_database()
    from app import r1b_finalization
    from app.db import SessionLocal
    from app.models import ScoreEvent, Submission, TeamRoundState
    await reset_data(); team_id, _, turn_id, now = await seed_active()
    await save(team_id, turn_id, 1, answer)
    from app.timing import utc_now
    expiry = utc_now() + timedelta(seconds=2)
    monkeypatch.setattr(r1b_finalization, "utc_now", lambda: expiry)
    assert await expire_with_worker(expiry) == 1
    async with SessionLocal() as db:
        submission = await db.scalar(select(Submission).where(Submission.team_id == team_id))
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        assert (submission.result, submission.points, submission.answer_text) == (result, points, answer)
        assert submission.submitted_at is None
        assert submission.timed_out_at == expiry - timedelta(milliseconds=1)
        assert submission.recorded_at == expiry
        assert submission.finalization_source == ("TIMEOUT_EMPTY" if not answer else "TIMEOUT_DRAFT")
        assert submission.draft_version == 1
        assert state.expired_at == submission.timed_out_at
        assert state.finalized_at == expiry
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 1


async def test_clear_is_latest_and_stale_versions_or_turns_cannot_overwrite(monkeypatch):
    require_disposable_test_database()
    from app import r1b_finalization
    from app.api import r1b
    from app.db import SessionLocal
    from app.models import R1BDraft, Submission
    await reset_data(); team_id, _, turn_id, now = await seed_active()
    await save(team_id, turn_id, 2, "42")
    with pytest.raises(HTTPException) as stale:
        await save(team_id, turn_id, 1, "41")
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException):
        await save(team_id, str(uuid4()), 3, "41")
    await save(team_id, turn_id, 3, "")
    async with SessionLocal() as db:
        draft = await db.scalar(select(R1BDraft))
        assert (draft.version, draft.answer_text) == (3, "")
    from app.timing import utc_now
    processing = utc_now() + timedelta(seconds=2)
    monkeypatch.setattr(r1b_finalization, "utc_now", lambda: processing)
    await expire_with_worker(processing)
    async with SessionLocal() as db:
        outcome = await db.scalar(select(Submission))
        assert (outcome.result, outcome.points, outcome.draft_version) == ("UNANSWERED", 0, 3)


async def test_only_current_slot_and_team_can_access_draft():
    require_disposable_test_database()
    from app.api import r1b
    from app.db import SessionLocal
    await reset_data(); team_id, other_id, turn_id, _ = await seed_active()
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as wrong_slot:
            await r1b.get_draft({"team_id": team_id, "slot": 2}, db)
        assert wrong_slot.value.status_code == 403
    async with SessionLocal() as db:
        with pytest.raises(HTTPException):
            await r1b.get_draft({"team_id": other_id, "slot": 1}, db)
    await save(team_id, turn_id, 1, "42")


@pytest.mark.parametrize("skip", [False, True])
async def test_delayed_autosave_cannot_change_manual_submit_or_skip(skip):
    require_disposable_test_database()
    from app.api import r1b
    from app.db import SessionLocal
    from app.models import ScoreEvent, Submission
    await reset_data(); team_id, _, turn_id, _ = await seed_active()
    await save(team_id, turn_id, 1, "41")
    async with SessionLocal() as db:
        result = await r1b.submit(r1b.SubmitNumeric(answer="42", skip=skip),
                                  {"team_id": team_id, "slot": 1}, db)
        assert result["result"] == ("SKIPPED" if skip else "CORRECT")
    with pytest.raises(HTTPException):
        await save(team_id, turn_id, 2, "0")
    async with SessionLocal() as db:
        outcome = await db.scalar(select(Submission))
        assert outcome.answer_text == "42"
        assert outcome.finalization_source == ("SKIP" if skip else "MANUAL")
        assert await db.scalar(select(func.count()).select_from(Submission)) == 1
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 1


async def test_concurrent_workers_are_idempotent_and_question_stays_closed(monkeypatch):
    require_disposable_test_database()
    from app import r1b_finalization
    from app.api import r1b
    from app.db import SessionLocal
    from app.models import ScoreEvent, Submission, TeamRoundState
    await reset_data(); team_id, _, turn_id, now = await seed_active()
    await save(team_id, turn_id, 1, "42")
    async with SessionLocal() as setup:
        state = await setup.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        state.remaining_time_ms = 1; state.clock_running_since = now
        state.team_deadline = now + timedelta(milliseconds=1); await setup.commit()
    monkeypatch.setattr(r1b_finalization, "utc_now", lambda: now + timedelta(seconds=1))
    async def worker():
        async with SessionLocal() as db:
            count = await r1b_finalization.reconcile_r1b_batch(db)
            await db.commit(); return count
    assert sum(await asyncio.gather(worker(), worker())) == 1
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Submission)) == 1
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 1
    async with SessionLocal() as db:
        with pytest.raises(HTTPException):
            await r1b.question({"team_id": team_id, "slot": 1}, db)


async def test_continuous_handover_expiry_creates_no_outcome(monkeypatch):
    require_disposable_test_database()
    from app import r1b_finalization
    from app.db import SessionLocal
    from app.models import ScoreEvent, Submission, TeamRoundState
    await reset_data(); team_id, _, _, now = await seed_active()
    async with SessionLocal() as db:
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        state.turn_status = "WAITING"; state.current_player = 2
        state.current_question_position = 2; state.current_turn_id = None
        state.remaining_time_ms = 1; state.clock_running_since = now
        state.team_deadline = now + timedelta(milliseconds=1); await db.commit()
    monkeypatch.setattr(r1b_finalization, "utc_now", lambda: now + timedelta(seconds=1))
    async with SessionLocal() as db:
        await r1b_finalization.reconcile_r1b_batch(db); await db.commit()
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        assert state.turn_status == "EXPIRED"
        assert state.finalization_source == "TIMEOUT_HANDOVER"
        assert await db.scalar(select(func.count()).select_from(Submission)) == 0
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 0


async def test_global_pause_and_pause_during_handover_do_not_expire(monkeypatch):
    require_disposable_test_database()
    from app import r1b_finalization
    from app.db import SessionLocal
    from app.models import Round, Submission, TeamRoundState
    await reset_data(); team_id, _, _, now = await seed_active(mode="PAUSE_DURING_HANDOVER")
    async with SessionLocal() as db:
        round_obj = await db.scalar(select(Round).where(Round.code == "R1B"))
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        round_obj.status = "PAUSED"; round_obj.paused_at = now
        state.remaining_time_ms = 1; state.clock_running_since = now
        state.team_deadline = now + timedelta(milliseconds=1); await db.commit()
    monkeypatch.setattr(r1b_finalization, "utc_now", lambda: now + timedelta(hours=1))
    async with SessionLocal() as db:
        assert await r1b_finalization.reconcile_r1b_batch(db) == 0
        await db.commit()
    async with SessionLocal() as db:
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        assert state.turn_status == "ACTIVE"
        assert await db.scalar(select(func.count()).select_from(Submission)) == 0

    # Live PAUSE_DURING_HANDOVER waiting has no running clock and cannot expire.
    async with SessionLocal() as db:
        round_obj = await db.scalar(select(Round).where(Round.code == "R1B"))
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        round_obj.status = "LIVE"; round_obj.paused_at = None
        state.turn_status = "WAITING"; state.current_player = 2
        state.current_question_position = 2; state.current_turn_id = None
        state.remaining_time_ms = 1; state.clock_running_since = None
        state.team_deadline = None; await db.commit()
    async with SessionLocal() as db:
        assert await r1b_finalization.reconcile_r1b_batch(db) == 0
        await db.commit()
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        assert state.turn_status == "WAITING"


async def test_lifespan_worker_recovers_disconnected_overdue_team(monkeypatch):
    require_disposable_test_database()
    from app import main, r1b_finalization
    from app.db import SessionLocal
    from app.models import Submission, TeamRoundState
    from app.timing import utc_now
    await reset_data(); team_id, _, turn_id, _ = await seed_active()
    await save(team_id, turn_id, 1, "42")
    processing = utc_now() + timedelta(seconds=2)
    async with SessionLocal() as db:
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        state.remaining_time_ms = 1
        state.clock_running_since = processing - timedelta(milliseconds=2)
        state.team_deadline = processing - timedelta(milliseconds=1)
        await db.commit()
    monkeypatch.setattr(r1b_finalization, "utc_now", lambda: processing)
    monkeypatch.setattr(main.settings, "r1a_reconciliation_interval_seconds", 0.01)
    stop = asyncio.Event(); task = asyncio.create_task(main._r1a_reconciliation_loop(stop))
    try:
        for _ in range(50):
            async with SessionLocal() as db:
                outcome = await db.scalar(select(Submission).where(Submission.team_id == team_id))
            if outcome:
                break
            await asyncio.sleep(0.01)
        assert outcome.result == "CORRECT"
    finally:
        stop.set(); await task
