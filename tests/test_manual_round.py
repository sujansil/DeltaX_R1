"""Run against a migrated local database with RUN_DB_TESTS=1; all changes roll back."""
import os
from datetime import timedelta
import pytest
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.timing import utc_now
from app.api.r1a import position_at
from app.models import Round
from tests.db_guard import require_disposable_test_database


def test_manual_question_clock():
    now = utc_now()
    r = Round(code="R1A", status="LIVE", current_position=5,
              question_started_at=now, started_at=now - timedelta(minutes=10))
    assert position_at(r, now) == 5
    assert position_at(r, now + timedelta(seconds=29, milliseconds=999)) == 5
    assert position_at(r, now + timedelta(seconds=30)) is None
    assert position_at(r, now + timedelta(hours=1)) is None
    assert r.current_position == 5


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.database
@pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires migrated PostgreSQL")
async def test_manual_flow_and_admin_audit():
    from dataclasses import replace
    from app.competition_rules import configured_rules
    require_disposable_test_database()
    from app.main import app
    from app.db import engine, get_db
    from app.dependencies import require_admin, require_player
    from app.models import Team, Question, QuestionOption, Submission
    async with engine.connect() as conn:
        outer = await conn.begin()
        async with AsyncSession(bind=conn, expire_on_commit=False,
                                join_transaction_mode="create_savepoint") as db:
            async def override_db():
                yield db
            app.dependency_overrides[get_db] = override_db
            app.dependency_overrides[require_admin] = lambda: {"role": "admin"}
            team = Team(code="TEST-MANUAL-AUDIT", name="Manual test", login_username="test-manual-audit")
            db.add(team)
            await db.flush()
            rules = configured_rules()
            rules = replace(rules, r1a=replace(rules.r1a, advance_mode="MANUAL"))
            r = Round(code="R1A", rules_version=rules.version,
                      rules_snapshot=rules.snapshot_json())
            db.add(r)
            for position in (1, 2):
                question = Question(round_code="R1A", position=position, answer_type="MCQ")
                db.add(question)
                await db.flush()
                option = QuestionOption(question_id=question.id, position=1, option_text="Test answer")
                db.add(option)
                await db.flush()
                question.correct_option_id = option.id
            await db.flush()
            app.dependency_overrides[require_player] = lambda: {"role": "team", "team_id": team.id}
            try:
                r.status = "LIVE"
                r.current_position = 1
                r.question_started_at = utc_now()
                await db.commit()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                    early = await c.post("/api/admin/r1a/next", json={"expected_position": 1})
                    assert early.status_code == 409
                    r.question_started_at = utc_now() - timedelta(seconds=31)
                    await db.commit()
                    state = (await c.get("/api/r1a/state")).json()
                    assert state["status"] == "WAITING"
                    q = await db.scalar(select(Question).where(Question.round_code == "R1A", Question.position == 1))
                    late = await c.post("/api/r1a/submit", json={"option_id": q.correct_option_id})
                    assert late.status_code == 409
                    assert (await c.post("/api/admin/r1a/next", json={"expected_position": 1})).status_code == 200
                    assert (await c.post("/api/admin/r1a/next", json={"expected_position": 1})).status_code == 409
                    q2 = await db.scalar(select(Question).where(Question.round_code == "R1A", Question.position == 2))
                    result = await c.post("/api/r1a/submit", json={"option_id": q2.correct_option_id})
                    assert result.status_code == 200, result.text
                    audit = (await c.get("/api/admin/submissions", params={"round_code": "R1A", "team_id": team.id})).json()
                    assert audit["total"] == 1
                    assert audit["items"][0]["option_id"] == q2.correct_option_id
                    assert audit["items"][0]["submitted_at"]
                    db.add(Submission(team_id=team.id, round_code="R1B", question_position=1,
                                      answer_text="12.75", result="WRONG", points=-1500))
                    await db.commit()
                    audit = (await c.get("/api/admin/submissions", params={"round_code": "R1B", "team_id": team.id})).json()
                    assert audit["items"][0]["answer"] == "12.75"
                    app.dependency_overrides.pop(require_admin)
                    assert (await c.get("/api/admin/submissions")).status_code in (401, 403)
            finally:
                app.dependency_overrides.clear()
        await outer.rollback()
    await engine.dispose()
