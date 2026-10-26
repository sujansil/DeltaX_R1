import asyncio
import os
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from tests.db_guard import require_disposable_test_database


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]

TIMEOUT = 3


async def reset_data():
    from app.db import SessionLocal
    from app.models import (AdminAction, LeaderboardSnapshot, Question, QuestionOption,
                            Round, ScoreEvent, Submission, Team, TeamRoundState)

    async with SessionLocal() as db:
        for model in (AdminAction, LeaderboardSnapshot, ScoreEvent, Submission,
                      TeamRoundState, QuestionOption, Question, Round, Team):
            await db.execute(delete(model))
        await db.commit()


async def seed_r1a(*, expired=False):
    from app.db import SessionLocal
    from app.models import Question, QuestionOption, Round, Team
    from app.timing import utc_now

    async with SessionLocal() as db:
        team = Team(code="RACE-A", name="Race A", login_username="race-a")
        db.add(team)
        await db.flush()
        option_ids = []
        for position in (1, 2):
            question = Question(round_code="R1A", position=position, answer_type="MCQ")
            db.add(question)
            await db.flush()
            option = QuestionOption(question_id=question.id, position=1, option_text="correct")
            db.add(option)
            await db.flush()
            question.correct_option_id = option.id
            option_ids.append(option.id)
        now = utc_now()
        db.add(Round(code="R1A", status="LIVE", started_at=now,
                     current_position=1,
                     question_started_at=now - timedelta(seconds=31 if expired else 0)))
        await db.commit()
        return team.id, option_ids


async def seed_r1b(action):
    from app.db import SessionLocal
    from app.models import Question, Round, Team, TeamRoundState
    from app.timing import utc_now

    async with SessionLocal() as db:
        team = Team(code="RACE-B", name="Race B", login_username="race-b")
        db.add(team)
        await db.flush()
        now = utc_now()
        db.add(Round(code="R1B", status="LIVE", started_at=now))
        question = Question(round_code="R1B", position=1, answer_type="NUMERIC",
                            correct_numeric=Decimal("42"))
        db.add(question)
        if action != "start":
            db.add(TeamRoundState(
                team_id=team.id, round_code="R1B", current_player=1,
                current_question_position=1,
                turn_status="WAITING" if action == "ready" else "ACTIVE",
                team_started_at=now, team_deadline=now + timedelta(minutes=28),
                remaining_time_ms=28 * 60 * 1000,
                clock_started_at=now,
                clock_running_since=now,
                ready_at=now if action == "submit" else None,
            ))
        await db.commit()
        return team.id


def gate_lock(monkeypatch, module):
    original = module.lock_round
    acquired = asyncio.Event()
    release = asyncio.Event()

    async def gated(*args, **kwargs):
        row = await original(*args, **kwargs)
        acquired.set()
        await release.wait()
        return row

    monkeypatch.setattr(module, "lock_round", gated)
    return original, acquired, release


async def assert_waiting(task):
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done, "operation unexpectedly bypassed the held PostgreSQL lock"


async def run_admin_transition(name, round_code, db):
    from app.api import admin

    data = admin.RoundAction(round_code=round_code)
    if name == "pause":
        return await admin.pause_round(data, user={"role": "admin"}, db=db)
    return await admin.end_round(data, user={"role": "admin"}, db=db)


async def counts(round_code):
    from app.db import SessionLocal
    from app.models import ScoreEvent, Submission

    async with SessionLocal() as db:
        submissions = await db.scalar(select(func.count()).select_from(Submission).where(
            Submission.round_code == round_code))
        scores = await db.scalar(select(func.count()).select_from(ScoreEvent).where(
            ScoreEvent.round_code == round_code))
        return submissions, scores


async def test_r1a_submit_orders_with_pause_and_end(monkeypatch):
    require_disposable_test_database()
    from app.api import admin, r1a
    from app.db import SessionLocal

    for transition in ("pause", "end"):
        # Player owns the shared round lock first: its complete submission wins.
        await reset_data()
        team_id, options = await seed_r1a()
        original, acquired, release = gate_lock(monkeypatch, r1a)
        async with SessionLocal() as player_db, SessionLocal() as admin_db:
            player = asyncio.create_task(r1a.submit(
                r1a.SubmitMCQ(option_id=options[0]), {"team_id": team_id}, player_db))
            await asyncio.wait_for(acquired.wait(), TIMEOUT)
            administrator = asyncio.create_task(run_admin_transition(transition, "R1A", admin_db))
            await assert_waiting(administrator)
            release.set()
            await asyncio.wait_for(asyncio.gather(player, administrator), TIMEOUT)
        assert await counts("R1A") == (1, 1)
        monkeypatch.setattr(r1a, "lock_round", original)

        # Admin owns the exclusive lock first: the later player observes pause/end.
        await reset_data()
        team_id, options = await seed_r1a()
        original, acquired, release = gate_lock(monkeypatch, admin)
        async with SessionLocal() as admin_db, SessionLocal() as player_db:
            administrator = asyncio.create_task(run_admin_transition(transition, "R1A", admin_db))
            await asyncio.wait_for(acquired.wait(), TIMEOUT)
            player = asyncio.create_task(r1a.submit(
                r1a.SubmitMCQ(option_id=options[0]), {"team_id": team_id}, player_db))
            await assert_waiting(player)
            release.set()
            await asyncio.wait_for(administrator, TIMEOUT)
            with pytest.raises(HTTPException) as rejected:
                await asyncio.wait_for(player, TIMEOUT)
            assert rejected.value.status_code == 409
        assert await counts("R1A") == (0, 0)
        monkeypatch.setattr(admin, "lock_round", original)


@pytest.mark.parametrize("action", ["start", "ready", "submit"])
@pytest.mark.parametrize("transition", ["pause", "end"])
async def test_r1b_player_actions_order_with_admin(monkeypatch, action, transition):
    require_disposable_test_database()
    from app.api import admin, r1b
    from app.db import SessionLocal

    async def player_call(team_id, db):
        if action == "start":
            return await r1b.start_team({"team_id": team_id}, db)
        if action == "ready":
            return await r1b.ready({"team_id": team_id}, db)
        return await r1b.submit(r1b.SubmitNumeric(answer="42"), {"team_id": team_id}, db)

    # Player first.
    await reset_data()
    team_id = await seed_r1b(action)
    original, acquired, release = gate_lock(monkeypatch, r1b)
    async with SessionLocal() as player_db, SessionLocal() as admin_db:
        player = asyncio.create_task(player_call(team_id, player_db))
        await asyncio.wait_for(acquired.wait(), TIMEOUT)
        administrator = asyncio.create_task(run_admin_transition(transition, "R1B", admin_db))
        await assert_waiting(administrator)
        release.set()
        await asyncio.wait_for(asyncio.gather(player, administrator), TIMEOUT)
    if action == "submit":
        assert await counts("R1B") == (1, 1)
    monkeypatch.setattr(r1b, "lock_round", original)

    # Admin first.
    await reset_data()
    team_id = await seed_r1b(action)
    original, acquired, release = gate_lock(monkeypatch, admin)
    async with SessionLocal() as admin_db, SessionLocal() as player_db:
        administrator = asyncio.create_task(run_admin_transition(transition, "R1B", admin_db))
        await asyncio.wait_for(acquired.wait(), TIMEOUT)
        player = asyncio.create_task(player_call(team_id, player_db))
        await assert_waiting(player)
        release.set()
        await asyncio.wait_for(administrator, TIMEOUT)
        with pytest.raises(HTTPException) as rejected:
            await asyncio.wait_for(player, TIMEOUT)
        assert rejected.value.status_code == 409
    if action == "submit":
        assert await counts("R1B") == (0, 0)
    monkeypatch.setattr(admin, "lock_round", original)


async def test_r1a_submit_and_reveal_are_ordered(monkeypatch):
    require_disposable_test_database()
    from app.api import admin, r1a
    from app.db import SessionLocal

    # Reveal first advances to Q2; the queued Q1 option is rejected without writes.
    await reset_data()
    team_id, options = await seed_r1a(expired=True)
    original, acquired, release = gate_lock(monkeypatch, admin)
    async with SessionLocal() as admin_db, SessionLocal() as player_db:
        reveal = asyncio.create_task(admin.next_question(
            admin.NextQuestion(expected_position=1), {"role": "admin"}, admin_db))
        await asyncio.wait_for(acquired.wait(), TIMEOUT)
        player = asyncio.create_task(r1a.submit(
            r1a.SubmitMCQ(option_id=options[0]), {"team_id": team_id}, player_db))
        await assert_waiting(player)
        release.set()
        await asyncio.wait_for(reveal, TIMEOUT)
        with pytest.raises(HTTPException):
            await asyncio.wait_for(player, TIMEOUT)
    assert await counts("R1A") == (0, 0)
    monkeypatch.setattr(admin, "lock_round", original)

    # Submit first commits Q1; an early reveal queued behind it remains rejected.
    await reset_data()
    team_id, options = await seed_r1a()
    original, acquired, release = gate_lock(monkeypatch, r1a)
    async with SessionLocal() as player_db, SessionLocal() as admin_db:
        player = asyncio.create_task(r1a.submit(
            r1a.SubmitMCQ(option_id=options[0]), {"team_id": team_id}, player_db))
        await asyncio.wait_for(acquired.wait(), TIMEOUT)
        reveal = asyncio.create_task(admin.next_question(
            admin.NextQuestion(expected_position=1), {"role": "admin"}, admin_db))
        await assert_waiting(reveal)
        release.set()
        await asyncio.wait_for(player, TIMEOUT)
        with pytest.raises(HTTPException):
            await asyncio.wait_for(reveal, TIMEOUT)
    assert await counts("R1A") == (1, 1)
    monkeypatch.setattr(r1a, "lock_round", original)


async def test_duplicate_submit_and_ready_are_serialized():
    require_disposable_test_database()
    from app.api import r1b
    from app.db import SessionLocal

    await reset_data()
    team_id = await seed_r1b("submit")
    async with SessionLocal() as first_db, SessionLocal() as second_db:
        calls = [
            asyncio.create_task(r1b.submit(r1b.SubmitNumeric(answer="42"), {"team_id": team_id}, first_db)),
            asyncio.create_task(r1b.submit(r1b.SubmitNumeric(answer="42"), {"team_id": team_id}, second_db)),
        ]
        results = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), TIMEOUT)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    assert await counts("R1B") == (1, 1)

    await reset_data()
    team_id, options = await seed_r1a()
    from app.api import r1a
    async with SessionLocal() as first_db, SessionLocal() as second_db:
        calls = [
            asyncio.create_task(r1a.submit(r1a.SubmitMCQ(option_id=options[0]), {"team_id": team_id}, first_db)),
            asyncio.create_task(r1a.submit(r1a.SubmitMCQ(option_id=options[0]), {"team_id": team_id}, second_db)),
        ]
        results = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), TIMEOUT)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    assert await counts("R1A") == (1, 1)

    await reset_data()
    team_id = await seed_r1b("ready")
    async with SessionLocal() as first_db, SessionLocal() as second_db:
        calls = [
            asyncio.create_task(r1b.ready({"team_id": team_id}, first_db)),
            asyncio.create_task(r1b.ready({"team_id": team_id}, second_db)),
        ]
        results = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), TIMEOUT)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1


async def test_r1a_deadline_is_checked_after_lock_wait():
    require_disposable_test_database()
    from app.api import r1a
    from app.db import SessionLocal
    from app.models import Round
    from app.timing import utc_now

    await reset_data()
    team_id, options = await seed_r1a()
    async with SessionLocal() as setup:
        round_obj = await setup.scalar(select(Round).where(Round.code == "R1A"))
        round_obj.question_started_at = utc_now() - timedelta(seconds=29, milliseconds=850)
        await setup.commit()

    async with SessionLocal() as blocker, SessionLocal() as player_db:
        await blocker.execute(select(Round).where(Round.code == "R1A").with_for_update())
        player = asyncio.create_task(r1a.submit(
            r1a.SubmitMCQ(option_id=options[0]), {"team_id": team_id}, player_db))
        await assert_waiting(player)
        await asyncio.sleep(0.2)
        await blocker.commit()
        with pytest.raises(HTTPException) as rejected:
            await asyncio.wait_for(player, TIMEOUT)
        assert rejected.value.status_code == 409
    assert await counts("R1A") == (0, 0)


@pytest.mark.parametrize("action", ["ready", "submit"])
async def test_r1b_deadline_is_checked_after_lock_wait(action):
    require_disposable_test_database()
    from app.api import r1b
    from app.db import SessionLocal
    from app.models import Round, TeamRoundState
    from app.timing import utc_now

    await reset_data()
    team_id = await seed_r1b(action)
    async with SessionLocal() as setup:
        state = await setup.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        now = utc_now()
        state.remaining_time_ms = 150
        state.clock_running_since = now
        state.team_deadline = now + timedelta(milliseconds=150)
        await setup.commit()

    async with SessionLocal() as blocker, SessionLocal() as player_db:
        await blocker.execute(select(Round).where(Round.code == "R1B").with_for_update())
        if action == "ready":
            player = asyncio.create_task(r1b.ready({"team_id": team_id}, player_db))
        else:
            player = asyncio.create_task(r1b.submit(
                r1b.SubmitNumeric(answer="42"), {"team_id": team_id}, player_db))
        await assert_waiting(player)
        await asyncio.sleep(0.2)
        await blocker.commit()
        with pytest.raises(HTTPException) as rejected:
            await asyncio.wait_for(player, TIMEOUT)
        assert rejected.value.status_code == 409

    async with SessionLocal() as check:
        state = await check.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        assert state.turn_status == "EXPIRED"
    # ACTIVE expiry now durably finalizes the latest server draft (empty here).
    assert await counts("R1B") == ((1, 1) if action == "submit" else (0, 0))


async def test_resume_is_ordered_with_player_actions(monkeypatch):
    require_disposable_test_database()
    from app.api import admin, r1a, r1b
    from app.db import SessionLocal
    from app.models import Round
    from app.timing import utc_now

    # Resume first makes R1A live before the queued submission validates status.
    await reset_data()
    team_id, options = await seed_r1a()
    async with SessionLocal() as setup:
        round_obj = await setup.scalar(select(Round).where(Round.code == "R1A"))
        round_obj.question_started_at = utc_now() - timedelta(seconds=5)
        round_obj.status = "PAUSED"
        round_obj.paused_at = utc_now() - timedelta(seconds=1)
        await setup.commit()
    original, acquired, release = gate_lock(monkeypatch, admin)
    async with SessionLocal() as admin_db, SessionLocal() as player_db:
        resume = asyncio.create_task(admin.resume_round(
            admin.RoundAction(round_code="R1A"), {"role": "admin"}, admin_db))
        await asyncio.wait_for(acquired.wait(), TIMEOUT)
        player = asyncio.create_task(r1a.submit(
            r1a.SubmitMCQ(option_id=options[0]), {"team_id": team_id}, player_db))
        await assert_waiting(player)
        release.set()
        await asyncio.wait_for(asyncio.gather(resume, player), TIMEOUT)
    assert await counts("R1A") == (1, 1)
    monkeypatch.setattr(admin, "lock_round", original)

    # A READY that owns the shared lock while paused rejects before resume proceeds.
    await reset_data()
    team_id = await seed_r1b("ready")
    async with SessionLocal() as setup:
        round_obj = await setup.scalar(select(Round).where(Round.code == "R1B"))
        round_obj.status = "PAUSED"
        round_obj.paused_at = utc_now() - timedelta(seconds=1)
        await setup.commit()
    original, acquired, release = gate_lock(monkeypatch, r1b)
    async with SessionLocal() as player_db, SessionLocal() as admin_db:
        player = asyncio.create_task(r1b.ready({"team_id": team_id}, player_db))
        await asyncio.wait_for(acquired.wait(), TIMEOUT)
        resume = asyncio.create_task(admin.resume_round(
            admin.RoundAction(round_code="R1B"), {"role": "admin"}, admin_db))
        await assert_waiting(resume)
        release.set()
        with pytest.raises(HTTPException) as rejected:
            await asyncio.wait_for(player, TIMEOUT)
        assert rejected.value.status_code == 409
        # FastAPI closes/rolls back the request session after the exception.
        await player_db.rollback()
        await asyncio.wait_for(resume, TIMEOUT)
    monkeypatch.setattr(r1b, "lock_round", original)


async def test_pause_after_r1a_expiry_does_not_reopen_question():
    require_disposable_test_database()
    from app.api import admin
    from app.api.r1a import position_at
    from app.db import SessionLocal
    from app.models import Round
    from app.timing import utc_now

    await reset_data()
    await seed_r1a(expired=True)
    async with SessionLocal() as db:
        before = await db.scalar(select(Round.question_started_at).where(Round.code == "R1A"))
    async with SessionLocal() as db:
        await admin.pause_round(admin.RoundAction(round_code="R1A"), {"role": "admin"}, db)
    await asyncio.sleep(0.01)
    async with SessionLocal() as db:
        await admin.resume_round(admin.RoundAction(round_code="R1A"), {"role": "admin"}, db)
    async with SessionLocal() as db:
        round_obj = await db.scalar(select(Round).where(Round.code == "R1A"))
        assert round_obj.question_started_at == before
        assert position_at(round_obj, utc_now()) is None
