import os
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

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


async def seed_identity(round_code, *, slots=(1,)):
    from app.db import SessionLocal
    from app.models import PlayerDevice, Team
    from app.security import create_token
    from app.timing import utc_now
    async with SessionLocal() as db:
        team = Team(code="BOOT", name="Bootstrap Team", login_username="bootstrap")
        db.add(team); await db.flush()
        tokens = {}
        for slot in slots:
            sid = uuid4().hex
            db.add(PlayerDevice(team_id=team.id, player_number=slot,
                                round_code=round_code, session_id=sid, active=True,
                                last_seen_at=utc_now(), expires_at=utc_now() + timedelta(minutes=10)))
            tokens[slot] = create_token(team.code, "team", team.id, sid, round_code, slot)
        await db.commit()
        return team.id, tokens


async def get_bootstrap(token):
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/player/bootstrap",
                                headers={"Authorization": "Bearer " + token})


async def seed_r1a(status, *, age_seconds=0, accepted=False, ended_at=None):
    from app.db import SessionLocal
    from app.models import Question, QuestionOption, Round, RoundEnrollment, Submission
    from app.timing import utc_now
    await reset_data(); team_id, tokens = await seed_identity("R1A")
    async with SessionLocal() as db:
        now = utc_now(); started = now - timedelta(seconds=age_seconds)
        q = Question(round_code="R1A", position=1, answer_type="MCQ")
        db.add(q); await db.flush()
        option = QuestionOption(question_id=q.id, position=1, option_text="Visible choice")
        db.add(option); await db.flush(); q.correct_option_id = option.id
        db.add(Round(code="R1A", status=status,
                     started_at=started if status != "DRAFT" else None,
                     current_position=1 if status != "DRAFT" else None,
                     question_started_at=started if status != "DRAFT" else None,
                     paused_at=now if status == "PAUSED" else None,
                     ended_at=ended_at or (now if status == "ENDED" else None)))
        db.add(RoundEnrollment(team_id=team_id, round_code="R1A", enrolled_at=started))
        if accepted:
            db.add(Submission(team_id=team_id, round_code="R1A", question_position=1,
                              selected_option_id=option.id, submitted_at=started,
                              recorded_at=started, result="CORRECT", points=1000))
        await db.commit()
    return tokens[1]


async def test_r1a_bootstrap_lifecycle_and_lost_submit_response():
    require_disposable_test_database()
    cases = [
        ("DRAFT", 0, False, "NOT_STARTED"),
        ("LIVE", 1, False, "ACTIVE_UNANSWERED"),
        ("LIVE", 1, True, "ACTIVE_ACCEPTED"),
        ("LIVE", 31, False, "BETWEEN_QUESTIONS"),
        ("PAUSED", 1, False, "PAUSED"),
        ("ENDED", 31, False, "ENDED"),
    ]
    for status, age, accepted, screen in cases:
        token = await seed_r1a(status, age_seconds=age, accepted=accepted)
        response = await get_bootstrap(token)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["contract_version"] == "1"
        assert data["rules"]["r1a"]["question_count"] == 23
        assert "correct_option_id" not in data["rules"]["r1a"]
        assert data["player_state"]["screen"] == screen
        assert data["identity"]["team_name"] == "Bootstrap Team"
        assert data["snapshot_version"] and data["snapshot_at"]
        if screen == "ACTIVE_UNANSWERED":
            assert data["question"]["options"][0]["text"] == "Visible choice"
            assert data["available_actions"] == ["SUBMIT"]
        else:
            assert data["question"] is None
        if accepted:
            assert data["own_outcome"]["accepted"] is True
            assert data["own_outcome"]["submission_id"]
            assert data["own_outcome"]["selected_option_id"]
            assert data["available_actions"] == []
        if screen == "BETWEEN_QUESTIONS":
            assert data["own_outcome"]["finalized"] is True
            assert data["own_outcome"]["accepted"] is False
            assert data["own_outcome"]["result"] == "UNANSWERED"
        assert "correct_option_id" not in response.text


async def seed_r1b(*, status="LIVE", turn_status=None, mode="CONTINUOUS",
                   current_player=1, slot_ids=(1,), running=False):
    from app.db import SessionLocal
    from app.models import Question, Round, TeamRoundState
    from app.timing import utc_now
    await reset_data(); team_id, tokens = await seed_identity("R1B", slots=slot_ids)
    async with SessionLocal() as db:
        now = utc_now()
        db.add(Round(code="R1B", status=status, started_at=now,
                     paused_at=now if status == "PAUSED" else None,
                     ended_at=now if status == "ENDED" else None,
                     r1b_timer_mode=mode))
        db.add(Question(round_code="R1B", position=1, answer_type="NUMERIC",
                        question_text="Restricted numeric question",
                        correct_numeric=Decimal("42"), tolerance_abs=Decimal("0"),
                        tolerance_rel=Decimal("0")))
        if turn_status:
            db.add(TeamRoundState(
                team_id=team_id, round_code="R1B", current_player=current_player,
                current_question_position=1, turn_status=turn_status,
                team_started_at=now, clock_started_at=now,
                remaining_time_ms=60_000,
                clock_running_since=now if running else None,
                team_deadline=now + timedelta(seconds=60) if running else None,
                ready_at=now if turn_status == "ACTIVE" else None,
                current_turn_id=str(uuid4()) if turn_status == "ACTIVE" else None,
                finished_at=now if turn_status in ("FINISHED", "EXPIRED") else None,
                expired_at=now if turn_status == "EXPIRED" else None,
            ))
        await db.commit()
    return team_id, tokens


async def test_r1b_active_owner_and_inactive_slot_visibility():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import R1BDraft, TeamRoundState
    team_id, tokens = await seed_r1b(turn_status="ACTIVE", slot_ids=(1, 2), running=True)
    async with SessionLocal() as db:
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        db.add(R1BDraft(team_id=team_id, question_position=1, player_number=1,
                        turn_id=state.current_turn_id, version=4, answer_text="41.",
                        accepted_at=state.ready_at))
        await db.commit()
    owner = (await get_bootstrap(tokens[1])).json()
    assert owner["player_state"]["screen"] == "ACTIVE"
    assert owner["question"]["text"] == "Restricted numeric question"
    assert owner["draft"]["answer"] == "41."
    assert owner["draft"]["version"] == 4
    assert set(owner["available_actions"]) == {"SUBMIT", "SKIP", "SAVE_DRAFT"}
    assert "correct_numeric" not in str(owner)
    assert "tolerance" not in str(owner)

    waiting = (await get_bootstrap(tokens[2])).json()
    assert waiting["player_state"]["screen"] == "WAITING_TURN"
    assert waiting["question"] is None
    assert waiting["draft"] is None
    assert waiting["available_actions"] == []


async def test_r1b_waiting_pause_finished_expired_and_global_end():
    require_disposable_test_database()
    cases = [
        ("LIVE", "WAITING", "CONTINUOUS", True, "HANDOVER_READY", True, False),
        ("LIVE", "WAITING", "PAUSE_DURING_HANDOVER", False, "HANDOVER_READY", False, True),
        ("PAUSED", "ACTIVE", "CONTINUOUS", True, "PAUSED", False, True),
        ("LIVE", "FINISHED", "CONTINUOUS", False, "FINISHED", False, False),
        ("LIVE", "EXPIRED", "CONTINUOUS", False, "EXPIRED", False, False),
        ("ENDED", "ACTIVE", "CONTINUOUS", False, "ENDED", False, False),
    ]
    for status, turn, mode, running, screen, timer_running, frozen in cases:
        _, tokens = await seed_r1b(status=status, turn_status=turn, mode=mode, running=running)
        response = await get_bootstrap(tokens[1])
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["player_state"]["screen"] == screen
        assert data["timer"]["running"] is timer_running
        assert data["timer"]["frozen"] is frozen
        assert data["question"] is None
        if screen == "HANDOVER_READY":
            assert data["available_actions"] == ["READY"]
        else:
            assert data["available_actions"] == []


async def test_r1b_lost_submit_response_recovers_own_outcome_only():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import Submission, TeamRoundState
    from app.timing import utc_now
    team_id, tokens = await seed_r1b(turn_status="WAITING", current_player=2,
                                     slot_ids=(1, 2), running=True)
    async with SessionLocal() as db:
        db.add(Submission(team_id=team_id, round_code="R1B", question_position=1,
                          player_number=1, answer_text="42", submitted_at=utc_now(),
                          recorded_at=utc_now(), result="CORRECT", points=4000,
                          finalization_source="MANUAL"))
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        state.current_question_position = 2
        await db.commit()
    previous = (await get_bootstrap(tokens[1])).json()
    assert previous["own_outcome"]["accepted"] is True
    assert previous["own_outcome"]["answer_text"] == "42"
    assert previous["own_outcome"]["submission_id"]
    assert previous["available_actions"] == []
    next_slot = (await get_bootstrap(tokens[2])).json()
    assert next_slot["own_outcome"]["finalized"] is False
    assert next_slot["question"] is None


async def test_bootstrap_rejects_revoked_and_cross_round_sessions_with_codes():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import PlayerDevice, Round
    from app.security import create_token
    await reset_data(); team_id, tokens = await seed_identity("R1A")
    async with SessionLocal() as db:
        db.add(Round(code="R1A", status="DRAFT")); await db.commit()
        device = await db.scalar(select(PlayerDevice).where(PlayerDevice.team_id == team_id))
        forged = create_token("BOOT", "team", team_id, device.session_id, "R1B", 1)
    cross = await get_bootstrap(forged)
    assert cross.status_code == 403
    assert cross.json()["detail"]["code"] == "ROUND_SCOPE_MISMATCH"
    async with SessionLocal() as db:
        device = await db.scalar(select(PlayerDevice).where(PlayerDevice.team_id == team_id))
        device.active = False; await db.commit()
    revoked = await get_bootstrap(tokens[1])
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "SESSION_INACTIVE"


async def test_bootstrap_score_total_is_authoritative_and_team_round_scoped():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import ScoreEvent, Team
    token = await seed_r1a("LIVE", age_seconds=1)
    async with SessionLocal() as db:
        team = await db.scalar(select(Team).where(Team.code == "BOOT"))
        other = Team(code="OTHER", name="Other team", login_username="other")
        db.add(other); await db.flush()
        db.add_all([
            ScoreEvent(team_id=team.id, round_code="R1A", reason="TEST", points=1000),
            ScoreEvent(team_id=team.id, round_code="R1A", reason="TEST", points=-500),
            ScoreEvent(team_id=team.id, round_code="R1B", reason="TEST", points=4000),
            ScoreEvent(team_id=other.id, round_code="R1A", reason="TEST", points=9999),
        ])
        await db.commit()
    response = await get_bootstrap(token)
    assert response.status_code == 200, response.text
    assert response.json()["score_total"] == 500
