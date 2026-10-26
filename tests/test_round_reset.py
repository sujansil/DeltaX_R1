import asyncio
import json
import os
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text

from tests.db_guard import require_disposable_test_database


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]


async def clear_database():
    from app.db import SessionLocal
    from app.models import (Admin, AdminAction, LeaderboardSnapshot, PlayerDevice,
                            Question, QuestionOption, R1BDraft, Round, RoundEnrollment,
                            ScoreEvent, Submission, Team, TeamRoundState)
    async with SessionLocal() as db:
        for model in (AdminAction, LeaderboardSnapshot, ScoreEvent, Submission,
                      R1BDraft, PlayerDevice, TeamRoundState, RoundEnrollment,
                      QuestionOption, Question, Round, Admin, Team):
            await db.execute(delete(model))
        await db.commit()


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def reset_fixture():
    require_disposable_test_database()
    await clear_database()
    yield
    await clear_database()


async def seed_reset_case(status="PAUSED"):
    from app.db import SessionLocal
    from app.models import (Admin, AdminAction, LeaderboardSnapshot, PlayerDevice,
                            Question, QuestionOption, R1BDraft, Round, RoundEnrollment,
                            ScoreEvent, Submission, Team, TeamRoundState)
    from app.timing import utc_now
    now = utc_now()
    async with SessionLocal() as db:
        admin = Admin(username="reset-admin", password_hash="kept-admin-password")
        team = Team(code="RST", name="Reset Team", login_username="reset-team",
                    password_hash="kept-team-password", activation_token_hash="a" * 64,
                    activation_expires_at=now + timedelta(days=1))
        db.add_all((admin, team)); await db.flush()
        r1a = Round(code="R1A", status=status, started_at=now, paused_at=now,
                    current_position=1, question_started_at=now,
                    leaderboard_published_at=now)
        r1b = Round(code="R1B", status=status, started_at=now, paused_at=now,
                    current_position=2, question_started_at=now,
                    leaderboard_published_at=now,
                    r1b_timer_mode="PAUSE_DURING_HANDOVER")
        db.add_all((r1a, r1b)); await db.flush()
        r1a_questions = []
        for position in range(1, 24):
            question = Question(round_code="R1A", position=position,
                                question_text="kept question", answer_type="MCQ",
                                question_image_url="/media/kept.png" if position == 1 else None)
            db.add(question); await db.flush()
            option = QuestionOption(question_id=question.id, position=1,
                                    option_text="kept option")
            db.add(option); await db.flush()
            question.correct_option_id = option.id
            r1a_questions.append(question)
        for position in range(1, 5):
            db.add(Question(round_code="R1B", position=position,
                            question_text="kept numeric", answer_type="NUMERIC",
                            correct_numeric=Decimal("42"), tolerance_abs=Decimal("0"),
                            tolerance_rel=Decimal("0")))
        await db.flush()
        q1 = r1a_questions[0]
        option = await db.scalar(select(QuestionOption).where(
            QuestionOption.question_id == q1.id))
        submission = Submission(team_id=team.id, round_code="R1A", question_position=1,
                                selected_option_id=option.id, result="CORRECT", points=100,
                                submitted_at=now, recorded_at=now)
        db.add(submission); await db.flush()
        db.add_all((
            ScoreEvent(team_id=team.id, round_code="R1A", submission_id=submission.id,
                       reason="test", points=100),
            R1BDraft(team_id=team.id, question_position=1, player_number=1,
                     turn_id=str(uuid4()), version=1, answer_text="42", accepted_at=now),
            TeamRoundState(team_id=team.id, round_code="R1B", current_player=1,
                           current_question_position=1, turn_status="ACTIVE"),
            RoundEnrollment(team_id=team.id, round_code="R1A", enrolled_at=now),
            LeaderboardSnapshot(round_code="R1A", published_at=now, data_json="[]"),
            PlayerDevice(team_id=team.id, player_number=1, round_code="R1A",
                         session_id="old-session", active=True, last_seen_at=now,
                         expires_at=now + timedelta(minutes=10)),
            AdminAction(admin_id=admin.id, action="PRESERVED_HISTORY", details="keep me"),
        ))
        await db.commit()
        return admin.id, team.id


def development(monkeypatch):
    from app.api import admin
    monkeypatch.setattr(admin.settings, "environment", "development")
    return admin


async def test_reset_requires_admin_and_development(monkeypatch):
    from app.main import app
    from app.security import create_token
    from app.api import admin
    await seed_reset_case()
    body = {"reason": "test reset", "confirmation": "RESET ROUND 1"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/admin/rounds/reset", json=body)).status_code in (401, 403)
        player = create_token("RST", "team", 1, "old-session", "R1A", 1)
        assert (await client.post("/api/admin/rounds/reset", json=body,
                                  headers={"Authorization": f"Bearer {player}"})).status_code == 403
        admin_token = create_token("reset-admin", "admin")
        monkeypatch.setattr(admin.settings, "environment", "production")
        assert (await client.post("/api/admin/rounds/reset", json=body,
                                  headers={"Authorization": f"Bearer {admin_token}"})).status_code == 404


async def test_wrong_confirmation_and_live_round_are_rejected(monkeypatch):
    from app.db import SessionLocal
    from app.models import Submission
    admin = development(monkeypatch)
    await seed_reset_case()
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as wrong:
            await admin.reset_round_one(admin.ResetRoundOneIn(
                reason="because", confirmation="reset round 1"), {"sub": "reset-admin"}, db)
        assert wrong.value.detail["code"] == "RESET_CONFIRMATION_INVALID"
        assert await db.scalar(select(func.count()).select_from(Submission)) == 1
    await clear_database(); await seed_reset_case(status="LIVE")
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as live:
            await admin.reset_round_one(admin.ResetRoundOneIn(
                reason="because", confirmation="RESET ROUND 1"), {"sub": "reset-admin"}, db)
        assert live.value.detail["code"] == "ROUND_LIVE"
        assert "Pause or end" in live.value.detail["message"]


async def test_reset_clears_state_preserves_configuration_and_revokes_tokens(monkeypatch):
    from app.db import SessionLocal
    from app.models import (Admin, AdminAction, LeaderboardSnapshot, PlayerDevice,
                            Question, QuestionOption, R1BDraft, Round, RoundEnrollment,
                            ScoreEvent, Submission, Team, TeamRoundState)
    from app.security import create_token
    from app.main import app
    admin = development(monkeypatch)
    admin_id, team_id = await seed_reset_case()
    old_token = create_token("RST", "team", team_id, "old-session", "R1A", 1)
    async with SessionLocal() as db:
        result = await admin.reset_round_one(admin.ResetRoundOneIn(
            reason="rerun after rehearsal", confirmation="RESET ROUND 1"),
            {"sub": "reset-admin"}, db)
        assert result["affected_records"] == {
            "player_sessions_revoked": 1, "score_events": 1, "submissions": 1,
            "numerical_drafts": 1, "relay_team_states": 1, "enrolments": 1,
            "leaderboard_snapshots": 1,
        }
    async with SessionLocal() as db:
        rounds = (await db.scalars(select(Round).order_by(Round.code))).all()
        assert all(r.status == "DRAFT" and r.started_at is None and r.ended_at is None
                   and r.paused_at is None and r.question_started_at is None
                   and r.current_position is None and r.leaderboard_published_at is None
                   for r in rounds)
        assert rounds[1].r1b_timer_mode == "PAUSE_DURING_HANDOVER"
        for model in (LeaderboardSnapshot, ScoreEvent, Submission, R1BDraft,
                      TeamRoundState, RoundEnrollment):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
        assert await db.scalar(select(func.count()).select_from(Team)) == 1
        assert await db.scalar(select(func.count()).select_from(Question)) == 27
        assert await db.scalar(select(func.count()).select_from(QuestionOption)) == 23
        team = await db.get(Team, team_id)
        assert (team.password_hash, team.activation_token_hash) == ("kept-team-password", "a" * 64)
        assert await db.scalar(select(func.count()).select_from(Admin)) == 1
        device = await db.scalar(select(PlayerDevice).where(PlayerDevice.session_id == "old-session"))
        assert not device.active and device.revoked_at and device.revoked_by_admin_id == admin_id
        actions = (await db.scalars(select(AdminAction).order_by(AdminAction.id))).all()
        assert [a.action for a in actions] == ["PRESERVED_HISTORY", "RESET_ROUND_ONE"]
        details = json.loads(actions[-1].details)
        assert details["reason"] == "rerun after rehearsal"
        assert details["affected_records"] == result["affected_records"]
    async with SessionLocal() as db:
        assert (await admin.start_round(admin.RoundAction(round_code="R1A"),
                                        {"sub": "reset-admin"}, db))["status"] == "LIVE"
    async with SessionLocal() as db:
        assert (await admin.start_round(admin.RoundAction(round_code="R1B"),
                                        {"sub": "reset-admin"}, db))["status"] == "LIVE"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/auth/team/me",
                                    headers={"Authorization": f"Bearer {old_token}"})
        assert response.status_code == 401


async def test_reset_waits_for_round_mutation_then_removes_its_write(monkeypatch):
    from app.db import SessionLocal
    from app.models import Round, Submission
    admin = development(monkeypatch)
    _, team_id = await seed_reset_case()
    async with SessionLocal() as player_db:
        await player_db.execute(select(Round).where(Round.code == "R1A").with_for_update(read=True))
        pending = asyncio.create_task(_run_reset(admin))
        await asyncio.sleep(0.1)
        assert not pending.done()
        player_db.add(Submission(team_id=team_id, round_code="R1A", question_position=2,
                                 result="WRONG", points=0))
        await player_db.commit()
        await asyncio.wait_for(pending, timeout=3)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Submission)) == 0


async def _run_reset(admin):
    from app.db import SessionLocal
    async with SessionLocal() as db:
        return await admin.reset_round_one(admin.ResetRoundOneIn(
            reason="ordering test", confirmation="RESET ROUND 1"),
            {"sub": "reset-admin"}, db)


async def test_reset_failure_rolls_back_every_change(monkeypatch):
    from app.db import SessionLocal
    from app.models import PlayerDevice, Round, Submission
    admin = development(monkeypatch)
    await seed_reset_case()
    async with SessionLocal() as db:
        await db.execute(text("""
            CREATE OR REPLACE FUNCTION fail_reset_audit() RETURNS trigger AS $$
            BEGIN
              IF NEW.action = 'RESET_ROUND_ONE' THEN RAISE EXCEPTION 'forced reset failure'; END IF;
              RETURN NEW;
            END; $$ LANGUAGE plpgsql
        """))
        await db.execute(text("""CREATE TRIGGER fail_reset_audit BEFORE INSERT ON admin_actions
                              FOR EACH ROW EXECUTE FUNCTION fail_reset_audit()"""))
        await db.commit()
    try:
        async with SessionLocal() as db:
            with pytest.raises(Exception, match="forced reset failure"):
                await admin.reset_round_one(admin.ResetRoundOneIn(
                    reason="failure test", confirmation="RESET ROUND 1"),
                    {"sub": "reset-admin"}, db)
            await db.rollback()
        async with SessionLocal() as db:
            assert await db.scalar(select(func.count()).select_from(Submission)) == 1
            assert await db.scalar(select(PlayerDevice.active)) is True
            assert (await db.scalar(select(Round.status).where(Round.code == "R1A"))) == "PAUSED"
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DROP TRIGGER IF EXISTS fail_reset_audit ON admin_actions"))
            await db.execute(text("DROP FUNCTION IF EXISTS fail_reset_audit()"))
            await db.commit()
