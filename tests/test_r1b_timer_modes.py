import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from tests.db_guard import require_disposable_test_database


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]

BUDGET_MS = 28 * 60 * 1000
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def reset_timer_data():
    from app.db import SessionLocal
    from app.models import (AdminAction, LeaderboardSnapshot, Question, QuestionOption,
                            Round, ScoreEvent, Submission, Team, TeamRoundState)

    async with SessionLocal() as db:
        for model in (AdminAction, LeaderboardSnapshot, ScoreEvent, Submission,
                      TeamRoundState, QuestionOption, Question, Round, Team):
            await db.execute(delete(model))
        await db.commit()


async def seed(mode="CONTINUOUS", status="LIVE"):
    from app.db import SessionLocal
    from app.models import Question, Round, Team

    async with SessionLocal() as db:
        team = Team(code="TIMER", name="Timer", login_username="timer")
        db.add(team)
        await db.flush()
        db.add(Round(code="R1B", status=status,
                     started_at=T0 if status != "DRAFT" else None,
                     r1b_timer_mode=mode))
        for position in range(1, 5):
            db.add(Question(round_code="R1B", position=position,
                            answer_type="NUMERIC", correct_numeric=Decimal("42")))
        await db.commit()
        return team.id


def server_clock(monkeypatch, instant=T0):
    from app.api import admin, r1b

    value = [instant]
    monkeypatch.setattr(r1b, "utc_now", lambda: value[0])
    monkeypatch.setattr(admin, "utc_now", lambda: value[0])
    return value


async def player_call(function, team_id, *args):
    from app.db import SessionLocal
    from app.api import r1b
    from app.models import TeamRoundState

    async with SessionLocal() as db:
        if args and isinstance(args[0], r1b.SubmitNumeric) and not args[0].turn_id:
            turn_id = await db.scalar(select(TeamRoundState.current_turn_id).where(
                TeamRoundState.team_id == team_id))
            args = (args[0].model_copy(update={"turn_id": turn_id}), *args[1:])
        return await function(*args, {"team_id": team_id}, db)


async def admin_call(function, *args):
    from app.db import SessionLocal

    async with SessionLocal() as db:
        return await function(*args, {"role": "admin"}, db)


async def prepare_and_ready(team_id):
    from app.api import r1b

    await player_call(r1b.start_team, team_id)
    return await player_call(r1b.ready, team_id)


async def read_state(team_id):
    from app.api import r1b
    return await player_call(r1b.state, team_id)


async def test_start_is_free_and_first_ready_starts_persisted_clock(monkeypatch):
    require_disposable_test_database()
    from app.api import r1b

    await reset_timer_data()
    team_id = await seed()
    clock = server_clock(monkeypatch)
    started = await player_call(r1b.start_team, team_id)
    assert started["turn_status"] == "WAITING"
    assert started["remaining_ms"] == BUDGET_MS
    assert started["clock_running"] is False
    assert started["team_deadline"] is None
    with pytest.raises(HTTPException):
        await player_call(r1b.question, team_id)

    clock[0] += timedelta(minutes=12)
    waiting = await read_state(team_id)
    assert waiting["remaining_ms"] == BUDGET_MS
    ready = await player_call(r1b.ready, team_id)
    assert ready["turn_status"] == "ACTIVE"
    assert ready["clock_running"] is True
    assert ready["remaining_ms"] == BUDGET_MS
    assert ready["clock_started_at"] == clock[0]
    assert ready["team_deadline"] == clock[0] + timedelta(minutes=28)
    question = await player_call(r1b.question, team_id)
    assert question["position"] == 1

    # A fresh session reconstructs state entirely from PostgreSQL.
    clock[0] += timedelta(seconds=3)
    recovered = await read_state(team_id)
    assert recovered["remaining_ms"] == BUDGET_MS - 3000
    assert recovered["clock_running"] is True


@pytest.mark.parametrize(
    ("mode", "expected_waiting", "expected_ready"),
    [
        ("CONTINUOUS", BUDGET_MS - 15_000, BUDGET_MS - 20_000),
        ("PAUSE_DURING_HANDOVER", BUDGET_MS - 10_000, BUDGET_MS - 10_000),
    ],
)
async def test_handover_mode_consumption(monkeypatch, mode, expected_waiting, expected_ready):
    from app.api import r1b

    await reset_timer_data()
    team_id = await seed(mode)
    clock = server_clock(monkeypatch)
    await prepare_and_ready(team_id)
    clock[0] += timedelta(seconds=10)
    await player_call(r1b.submit, team_id, r1b.SubmitNumeric(answer="42"))

    clock[0] += timedelta(seconds=5)
    waiting = await read_state(team_id)
    assert waiting["turn_status"] == "WAITING"
    assert waiting["remaining_ms"] == expected_waiting
    assert waiting["clock_running"] is (mode == "CONTINUOUS")
    assert (waiting["team_deadline"] is not None) is (mode == "CONTINUOUS")

    clock[0] += timedelta(seconds=5)
    ready = await player_call(r1b.ready, team_id)
    assert ready["remaining_ms"] == expected_ready
    assert ready["clock_running"] is True


@pytest.mark.parametrize("mode", ["CONTINUOUS", "PAUSE_DURING_HANDOVER"])
@pytest.mark.parametrize("phase", ["ACTIVE", "WAITING"])
async def test_global_pause_freezes_both_modes_and_phases(monkeypatch, mode, phase):
    from app.api import admin, r1b

    await reset_timer_data()
    team_id = await seed(mode)
    clock = server_clock(monkeypatch)
    await prepare_and_ready(team_id)
    clock[0] += timedelta(seconds=5)
    if phase == "WAITING":
        await player_call(r1b.submit, team_id, r1b.SubmitNumeric(skip=True))

    clock[0] += timedelta(seconds=5)
    before_pause = await read_state(team_id)
    await admin_call(admin.pause_round, admin.RoundAction(round_code="R1B"))
    clock[0] += timedelta(seconds=20)
    paused = await read_state(team_id)
    assert paused["remaining_ms"] == before_pause["remaining_ms"]
    assert paused["clock_running"] is False
    assert paused["team_deadline"] is None

    await admin_call(admin.resume_round, admin.RoundAction(round_code="R1B"))
    resumed = await read_state(team_id)
    assert resumed["remaining_ms"] == before_pause["remaining_ms"]
    should_run = phase == "ACTIVE" or mode == "CONTINUOUS"
    assert resumed["clock_running"] is should_run
    clock[0] += timedelta(seconds=5)
    later = await read_state(team_id)
    expected = before_pause["remaining_ms"] - (5000 if should_run else 0)
    assert later["remaining_ms"] == expected


async def test_multiple_global_pause_cycles_do_not_change_budget(monkeypatch):
    from app.api import admin

    await reset_timer_data()
    team_id = await seed()
    clock = server_clock(monkeypatch)
    await prepare_and_ready(team_id)
    clock[0] += timedelta(seconds=4)

    for pause_seconds in (7, 11, 3):
        before = await read_state(team_id)
        await admin_call(admin.pause_round, admin.RoundAction(round_code="R1B"))
        clock[0] += timedelta(seconds=pause_seconds)
        assert (await read_state(team_id))["remaining_ms"] == before["remaining_ms"]
        await admin_call(admin.resume_round, admin.RoundAction(round_code="R1B"))
        assert (await read_state(team_id))["remaining_ms"] == before["remaining_ms"]

    clock[0] += timedelta(seconds=6)
    assert (await read_state(team_id))["remaining_ms"] == BUDGET_MS - 10_000


@pytest.mark.parametrize("offset_ms", [999, 1000, 1001])
async def test_submission_expiry_boundary(monkeypatch, offset_ms):
    from app.api import r1b
    from app.db import SessionLocal
    from app.models import TeamRoundState

    await reset_timer_data()
    team_id = await seed()
    clock = server_clock(monkeypatch)
    await prepare_and_ready(team_id)
    async with SessionLocal() as db:
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        state.remaining_time_ms = 1000
        state.clock_running_since = T0
        state.team_deadline = T0 + timedelta(seconds=1)
        await db.commit()

    clock[0] = T0 + timedelta(milliseconds=offset_ms)
    if offset_ms < 1000:
        result = await player_call(r1b.submit, team_id, r1b.SubmitNumeric(skip=True))
        assert result["position"] == 1
    else:
        with pytest.raises(HTTPException) as rejected:
            await player_call(r1b.submit, team_id, r1b.SubmitNumeric(skip=True))
        assert rejected.value.status_code == 409
        assert (await read_state(team_id))["turn_status"] == "EXPIRED"


async def test_continuous_waiting_expires_and_hides_remaining_questions(monkeypatch):
    from app.api import r1b
    from app.db import SessionLocal
    from app.models import TeamRoundState

    await reset_timer_data()
    team_id = await seed("CONTINUOUS")
    clock = server_clock(monkeypatch)
    await prepare_and_ready(team_id)
    clock[0] += timedelta(seconds=1)
    await player_call(r1b.submit, team_id, r1b.SubmitNumeric(skip=True))
    async with SessionLocal() as db:
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        state.remaining_time_ms = 100
        state.clock_running_since = clock[0]
        state.team_deadline = clock[0] + timedelta(milliseconds=100)
        await db.commit()
    clock[0] += timedelta(milliseconds=100)
    expired = await read_state(team_id)
    assert expired["turn_status"] == "EXPIRED"
    assert expired["remaining_ms"] == 0
    with pytest.raises(HTTPException):
        await player_call(r1b.ready, team_id)


async def test_timer_mode_admin_api_and_locking(monkeypatch):
    from app.api import admin
    from app.db import SessionLocal
    from app.models import Round

    await reset_timer_data()
    await seed(status="DRAFT")
    server_clock(monkeypatch)
    initial = await admin_call(admin.get_r1b_timer)
    assert initial["timer_mode"] == "CONTINUOUS"
    changed = await admin_call(
        admin.set_r1b_timer,
        admin.R1BTimerConfig(timer_mode="PAUSE_DURING_HANDOVER"),
    )
    assert changed["timer_mode"] == "PAUSE_DURING_HANDOVER"
    await admin_call(admin.start_round, admin.RoundAction(round_code="R1B"))

    for status in ("LIVE", "PAUSED"):
        if status == "PAUSED":
            async with SessionLocal() as db:
                round_obj = await db.scalar(select(Round).where(Round.code == "R1B"))
                round_obj.status = "PAUSED"
                round_obj.paused_at = T0
                await db.commit()
        with pytest.raises(HTTPException) as rejected:
            await admin_call(
                admin.set_r1b_timer,
                admin.R1BTimerConfig(timer_mode="CONTINUOUS"),
            )
        assert rejected.value.status_code == 409


async def test_concurrent_first_ready_does_not_restart_or_replenish(monkeypatch):
    from app.api import r1b
    from app.db import SessionLocal

    await reset_timer_data()
    team_id = await seed()
    clock = server_clock(monkeypatch)
    await player_call(r1b.start_team, team_id)
    clock[0] += timedelta(minutes=5)
    async with SessionLocal() as first, SessionLocal() as second:
        results = await asyncio.wait_for(asyncio.gather(
            r1b.ready({"team_id": team_id}, first),
            r1b.ready({"team_id": team_id}, second),
            return_exceptions=True,
        ), 3)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, HTTPException) for result in results) == 1
    recovered = await read_state(team_id)
    assert recovered["clock_started_at"] == clock[0]
    assert recovered["remaining_ms"] == BUDGET_MS
