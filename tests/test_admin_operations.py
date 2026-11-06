import asyncio
import os
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from tests.db_guard import require_disposable_test_database


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def cleanup_admin_test_data():
    yield
    await reset_data()


async def reset_data():
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


async def seed_admin():
    from app.db import SessionLocal
    from app.models import Admin
    async with SessionLocal() as db:
        admin = Admin(username="ops", password_hash="unused")
        db.add(admin); await db.commit(); return admin.id


async def test_admin_endpoints_require_admin():
    require_disposable_test_database()
    from app.main import app
    await reset_data()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        assert (await client.get("/api/admin/questions")).status_code in (401, 403)
        assert (await client.get("/api/admin/rounds/R1A/validate")).status_code in (401, 403)
        assert (await client.put("/api/admin/teams/1", json={"name": "x"})).status_code in (401, 403)


async def test_validation_numeric_rules_ownership_and_live_edit_lock():
    require_disposable_test_database()
    from app.api import admin
    from app.db import SessionLocal
    from app.models import Question, QuestionOption, Round
    await reset_data(); await seed_admin(); user = {"sub": "ops"}
    async with SessionLocal() as db:
        db.add_all((Round(code="R1A"), Round(code="R1B"))); await db.commit()
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as invalid:
            await admin.create_question(admin.QuestionCreate(
                round_code="R1B", position=1, answer_type="NUMERIC",
                correct_numeric="NaN", tolerance_abs="0", tolerance_rel="0"), user, db)
        assert invalid.value.detail["code"] == "INVALID_NUMERIC"
        await db.rollback()
        with pytest.raises(HTTPException):
            await admin.create_question(admin.QuestionCreate(
                round_code="R1B", position=1, answer_type="NUMERIC",
                correct_numeric="42", tolerance_abs="-1", tolerance_rel="0"), user, db)

    async with SessionLocal() as db:
        q1 = Question(round_code="R1A", position=1, answer_type="MCQ")
        q2 = Question(round_code="R1A", position=2, answer_type="MCQ")
        db.add_all((q1, q2)); await db.flush()
        o1 = QuestionOption(question_id=q1.id, position=1, option_text="one")
        o2 = QuestionOption(question_id=q2.id, position=1, option_text="two")
        db.add_all((o1, o2)); await db.commit()
        q1_id, o2_id = q1.id, o2.id
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as ownership:
            await admin.update_question(q1_id, admin.QuestionUpdate(correct_option_id=o2_id), user, db)
        assert ownership.value.detail["code"] == "CORRECT_OPTION_OWNERSHIP"

    async with SessionLocal() as db:
        for position in range(1, 5):
            db.add(Question(round_code="R1B", position=position, answer_type="NUMERIC",
                            correct_numeric=Decimal("42"), tolerance_abs=Decimal("0"),
                            tolerance_rel=Decimal("0")))
        await db.commit()
        report = await admin.validate_round("R1B", user, db)
        assert report["valid"] is True
        await admin.start_round(admin.RoundAction(round_code="R1B"), user, db)
        qid = await db.scalar(select(Question.id).where(Question.round_code == "R1B",
                                                        Question.position == 1))
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as locked:
            await admin.update_question(qid, admin.QuestionUpdate(question_text="changed"), user, db)
        assert locked.value.detail["code"] == "ROUND_CONTENT_LOCKED"


async def test_team_update_password_reset_preserves_progress_and_audits_actor():
    require_disposable_test_database()
    from app.api import admin
    from app.db import SessionLocal
    from app.models import AdminAction, PlayerDevice, Round, Submission, Team
    from app.timing import utc_now
    await reset_data(); admin_id = await seed_admin(); user = {"sub": "ops"}
    async with SessionLocal() as db:
        team = Team(code="T1", name="Team", login_username="team", password_hash="old")
        db.add(team); db.add_all((Round(code="R1A"), Round(code="R1B")))
        await db.flush(); team_id = team.id
        db.add(PlayerDevice(team_id=team_id, player_number=1, round_code="R1A",
                            session_id=uuid4().hex, active=True,
                            expires_at=utc_now() + timedelta(minutes=5)))
        db.add(Submission(team_id=team_id, round_code="R1A", question_position=1,
                          result="CORRECT", points=1000, submitted_at=utc_now(), recorded_at=utc_now()))
        await db.commit()
    async with SessionLocal() as db:
        updated = await admin.update_team(team_id, admin.TeamUpdate(name="Renamed", enabled=False), user, db)
        assert updated["enabled"] is False
        result = await admin.reset_team_password(team_id, admin.PasswordResetIn(new_password="new password"), user, db)
        assert result["revoked_sessions"] == 1
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Submission)) == 1
        assert not await db.scalar(select(PlayerDevice.active).where(PlayerDevice.team_id == team_id))
        actions = (await db.scalars(select(AdminAction).where(AdminAction.admin_id == admin_id))).all()
        assert {a.action for a in actions} >= {"UPDATE_TEAM", "RESET_TEAM_PASSWORD"}
        assert all("new password" not in (a.details or "") for a in actions)
        team = await db.scalar(select(Team).where(Team.id == team_id))
        assert team.password_hash != "old"


async def seed_end_case(*, overdue):
    from app.db import SessionLocal
    from app.models import (Question, R1BDraft, Round, ScoreEvent, Submission,
                            Team, TeamRoundState)
    from app.timing import utc_now
    async with SessionLocal() as db:
        team = Team(code="END", name="End", login_username="end")
        db.add(team); await db.flush(); now = utc_now()
        db.add(Round(code="R1B", status="LIVE", started_at=now))
        for position in (1, 2):
            db.add(Question(round_code="R1B", position=position, answer_type="NUMERIC",
                            correct_numeric=Decimal("42"), tolerance_abs=Decimal("0"),
                            tolerance_rel=Decimal("0")))
        previous = Submission(team_id=team.id, round_code="R1B", question_position=1,
                              player_number=1, answer_text="42", result="CORRECT", points=4000,
                              submitted_at=now, recorded_at=now, finalization_source="MANUAL")
        db.add(previous); await db.flush()
        db.add(ScoreEvent(team_id=team.id, round_code="R1B", submission_id=previous.id,
                          reason="R1B_Q1_CORRECT", points=4000))
        turn_id = str(uuid4())
        db.add(TeamRoundState(team_id=team.id, round_code="R1B", current_player=2,
                              current_question_position=2, turn_status="ACTIVE",
                              team_started_at=now, clock_started_at=now, ready_at=now,
                              remaining_time_ms=1000,
                              clock_running_since=now - timedelta(seconds=2) if overdue else now,
                              team_deadline=now - timedelta(seconds=1) if overdue else now + timedelta(minutes=1),
                              current_turn_id=turn_id))
        db.add(R1BDraft(team_id=team.id, question_position=2, player_number=2,
                        turn_id=turn_id, version=1, answer_text="42", accepted_at=now))
        await db.commit(); return team.id


@pytest.mark.parametrize("overdue", [False, True])
async def test_end_policy_reconciles_only_due_drafts_and_is_permanent(overdue):
    require_disposable_test_database()
    from app.api import admin, r1b
    from app.db import SessionLocal
    from app.models import Round, ScoreEvent, Submission, TeamRoundState
    await reset_data(); await seed_admin(); team_id = await seed_end_case(overdue=overdue)
    user = {"sub": "ops"}
    async with SessionLocal() as db:
        await admin.end_round(admin.RoundAction(round_code="R1B"), user, db)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Submission)) == (2 if overdue else 1)
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == (2 if overdue else 1)
        state = await db.scalar(select(TeamRoundState).where(TeamRoundState.team_id == team_id))
        assert state.turn_status == ("EXPIRED" if overdue else "ACTIVE")
        assert state.clock_running_since is None
        assert (await db.scalar(select(Round.status).where(Round.code == "R1B"))) == "ENDED"
    async with SessionLocal() as db:
        with pytest.raises(HTTPException):
            await r1b.ready({"team_id": team_id, "slot": 2}, db)
    async with SessionLocal() as db:
        with pytest.raises(HTTPException):
            await admin.resume_round(admin.RoundAction(round_code="R1B"), user, db)


async def test_concurrent_publication_is_immutable_and_audited():
    require_disposable_test_database()
    from app.api import admin
    from app.db import SessionLocal
    from app.models import AdminAction, LeaderboardSnapshot, Round, Team
    await reset_data(); admin_id = await seed_admin(); user = {"sub": "ops"}
    async with SessionLocal() as db:
        db.add(Round(code="R1A", status="ENDED")); db.add(Team(code="P", name="P", login_username="p"))
        await db.commit()
    async def publish():
        async with SessionLocal() as db:
            return await admin.publish(admin.RoundAction(round_code="R1A"), user, db)
    first, second = await asyncio.gather(publish(), publish())
    assert first["publication_id"] != second["publication_id"]
    assert first["scope"] == {"round_code": "R1A", "immutable": True}
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot)) == 2
        assert await db.scalar(select(func.count()).select_from(AdminAction).where(
            AdminAction.admin_id == admin_id, AdminAction.action == "PUBLISH_LEADERBOARD")) == 2


async def test_final_publication_requires_ended_round_and_is_typed():
    require_disposable_test_database()
    from app.api import admin
    from app.db import SessionLocal
    from app.models import LeaderboardSnapshot, Round
    await reset_data(); await seed_admin(); user = {"sub": "ops"}
    async with SessionLocal() as db:
        db.add(Round(code="R1B", status="LIVE")); await db.commit()
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as rejected:
            await admin.publish(admin.RoundAction(round_code="R1B"), user, db)
        assert rejected.value.status_code == 409
        await db.rollback()
    async with SessionLocal() as db:
        round_obj = await db.scalar(select(Round).where(Round.code == "R1B"))
        round_obj.status = "ENDED"; await db.commit()
    async with SessionLocal() as db:
        await admin.publish(admin.RoundAction(round_code="R1B"), user, db)
    async with SessionLocal() as db:
        snapshot = await db.scalar(select(LeaderboardSnapshot))
        assert '"kind": "EXPLICIT_FINAL_PUBLICATION_V1"' in snapshot.data_json
