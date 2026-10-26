import asyncio
import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from tests.db_guard import require_disposable_test_database

pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]


async def reset_and_seed(round_code):
    from app.db import SessionLocal
    from app.models import (AdminAction, LeaderboardSnapshot, PlayerDevice, Question,
                            QuestionOption, Round, ScoreEvent, Submission, Team,
                            TeamRoundState)
    from app.security import hash_password_async
    async with SessionLocal() as db:
        for model in (AdminAction, LeaderboardSnapshot, ScoreEvent, Submission,
                      PlayerDevice, TeamRoundState, QuestionOption, Question, Round, Team):
            await db.execute(delete(model))
        team = Team(code="SESSION", name="Session Team", login_username="session-team",
                    password_hash=await hash_password_async("correct horse"))
        db.add(team)
        # Server-owned lifecycle selects R1B only once R1A has ended.
        db.add(Round(code="R1A", status="ENDED" if round_code == "R1B" else "DRAFT"))
        db.add(Round(code="R1B", status="LIVE" if round_code == "R1B" else "DRAFT"))
        await db.commit()
        return team.id


async def client():
    from app.main import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def login(c):
    return await c.post("/api/auth/team/login", json={
        "username": "session-team", "password": "correct horse"
    })


async def test_r1a_simultaneous_logins_only_one_succeeds():
    require_disposable_test_database()
    await reset_and_seed("R1A")
    async with await client() as c:
        responses = await asyncio.gather(*(login(c) for _ in range(6)))
    assert [r.status_code for r in responses].count(200) == 1
    assert [r.status_code for r in responses].count(409) == 5
    assert next(r for r in responses if r.status_code == 200).json()["slot"] == 1


async def test_r1b_simultaneous_logins_are_four_distinct_slots():
    require_disposable_test_database()
    await reset_and_seed("R1B")
    async with await client() as c:
        responses = await asyncio.gather(*(login(c) for _ in range(8)))
    successes = [r.json() for r in responses if r.status_code == 200]
    assert len(successes) == 4
    assert {r["slot"] for r in successes} == {1, 2, 3, 4}
    assert [r.status_code for r in responses].count(409) == 4


async def test_logout_expiry_replacement_and_late_old_requests():
    require_disposable_test_database()
    team_id = await reset_and_seed("R1A")
    from app.db import SessionLocal
    from app.models import PlayerDevice
    from app.timing import utc_now
    async with await client() as c:
        first = (await login(c)).json()
        old_headers = {"Authorization": "Bearer " + first["access_token"]}
        assert (await c.post("/api/auth/team/logout", headers=old_headers)).status_code == 200
        second = (await login(c)).json()
        new_headers = {"Authorization": "Bearer " + second["access_token"]}
        assert second["session_id"] if "session_id" in second else second["slot"] == 1
        assert (await c.post("/api/auth/team/heartbeat", headers=old_headers)).status_code == 401
        assert (await c.post("/api/auth/team/logout", headers=old_headers)).status_code == 401
        assert (await c.get("/api/auth/team/me", headers=new_headers)).status_code == 200

        async with SessionLocal() as db:
            current = await db.scalar(select(PlayerDevice).where(
                PlayerDevice.team_id == team_id, PlayerDevice.active.is_(True)))
            current.expires_at = utc_now() - timedelta(seconds=1)
            await db.commit()
        third = await login(c)
        assert third.status_code == 200
        assert (await c.post("/api/auth/team/heartbeat", headers=new_headers)).status_code == 401


async def test_refresh_me_retains_identity_and_cross_round_is_rejected():
    require_disposable_test_database()
    await reset_and_seed("R1B")
    async with await client() as c:
        logged_in = (await login(c)).json()
        headers = {"Authorization": "Bearer " + logged_in["access_token"]}
        before = (await c.get("/api/auth/team/me", headers=headers)).json()
        assert (await c.post("/api/auth/team/heartbeat", headers=headers)).status_code == 200
        after = (await c.get("/api/auth/team/me", headers=headers)).json()
        assert (before["session_id"], before["slot"], before["round_code"]) == (
            after["session_id"], after["slot"], after["round_code"])
        assert (await c.get("/api/r1a/state", headers=headers)).status_code == 403


async def test_non_current_r1b_slot_cannot_ready_question_or_submit():
    require_disposable_test_database()
    await reset_and_seed("R1B")
    async with await client() as c:
        first = (await login(c)).json()
        second = (await login(c)).json()
        h1 = {"Authorization": "Bearer " + first["access_token"]}
        h2 = {"Authorization": "Bearer " + second["access_token"]}
        assert (await c.post("/api/r1b/start", headers=h1)).status_code == 200
        assert (await c.post("/api/r1b/ready", headers=h2)).status_code == 403
        assert (await c.get("/api/r1b/question", headers=h2)).status_code in (403, 409)
        assert (await c.post("/api/r1b/submit", headers=h2, json={"answer": "1"})).status_code == 403


async def protected_r1b_responses(c, headers):
    calls = [
        c.get("/api/r1b/state", headers=headers),
        c.post("/api/r1b/start", headers=headers),
        c.post("/api/r1b/ready", headers=headers),
        c.get("/api/r1b/question", headers=headers),
        c.post("/api/r1b/submit", headers=headers, json={"answer": "1"}),
        c.get("/api/leaderboard/published?round_code=R1B", headers=headers),
        c.get("/api/leaderboard/mine?round_code=R1B", headers=headers),
    ]
    return await asyncio.gather(*calls)


@pytest.mark.parametrize("invalidated_by", ["expiry", "admin-revocation"])
async def test_invalidated_jwt_is_rejected_on_every_player_path(invalidated_by):
    require_disposable_test_database()
    team_id = await reset_and_seed("R1B")
    from app.db import SessionLocal
    from app.models import PlayerDevice
    from app.security import create_token
    from app.timing import utc_now
    async with await client() as c:
        logged_in = (await login(c)).json()
        headers = {"Authorization": "Bearer " + logged_in["access_token"]}
        if invalidated_by == "expiry":
            async with SessionLocal() as db:
                row = await db.scalar(select(PlayerDevice).where(
                    PlayerDevice.team_id == team_id, PlayerDevice.active.is_(True)))
                row.expires_at = utc_now() - timedelta(seconds=1)
                await db.commit()
        else:
            admin_headers = {"Authorization": "Bearer " + create_token("test-admin", "admin")}
            response = await c.post(
                f"/api/admin/teams/{team_id}/sessions/revoke-all",
                headers=admin_headers, json={"reason": "deterministic test"})
            assert response.status_code == 200
            assert response.json()["revoked"] == 1
        responses = await protected_r1b_responses(c, headers)
        assert {r.status_code for r in responses} == {401}


async def test_cross_team_session_claim_is_rejected():
    require_disposable_test_database()
    team_id = await reset_and_seed("R1B")
    from app.db import SessionLocal
    from app.models import PlayerDevice, Team
    from app.security import create_token
    async with await client() as c:
        logged_in = (await login(c)).json()
        async with SessionLocal() as db:
            other = Team(code="OTHER", name="Other", login_username="other")
            db.add(other)
            await db.commit()
            await db.refresh(other)
            session_id = await db.scalar(select(PlayerDevice.session_id).where(
                PlayerDevice.team_id == team_id, PlayerDevice.active.is_(True)))
        forged = create_token("OTHER", "team", other.id, session_id, "R1B", logged_in["slot"])
        response = await c.get("/api/r1b/state", headers={"Authorization": "Bearer " + forged})
        assert response.status_code == 401


async def seed_active_submission_turn():
    team_id = await reset_and_seed("R1B")
    from app.db import SessionLocal
    from app.models import Question, TeamRoundState
    from uuid import uuid4
    from app.timing import utc_now
    from decimal import Decimal
    async with await client() as c:
        login_data = (await login(c)).json()
    now = utc_now()
    async with SessionLocal() as db:
        db.add(Question(round_code="R1B", position=1, answer_type="NUMERIC",
                        correct_numeric=Decimal("42")))
        db.add(TeamRoundState(
            team_id=team_id, round_code="R1B", current_player=1,
            current_question_position=1, turn_status="ACTIVE", team_started_at=now,
            remaining_time_ms=28 * 60 * 1000, clock_started_at=now,
            clock_running_since=now, team_deadline=now + timedelta(minutes=28), ready_at=now,
            current_turn_id=str(uuid4())))
        await db.commit()
    from app.security import decode_token
    return team_id, decode_token(login_data["access_token"])


async def test_revocation_and_submission_have_deterministic_transaction_order(monkeypatch):
    require_disposable_test_database()
    from fastapi import HTTPException
    from sqlalchemy import func
    from app.api import admin, r1b
    from app.db import SessionLocal
    from app.models import PlayerDevice, Submission

    # Submission owns the shared Round lock first: it commits, then revocation wins
    # for every later request.
    team_id, user = await seed_active_submission_turn()
    original = r1b.lock_round
    acquired, release = asyncio.Event(), asyncio.Event()
    async def gated_player(*args, **kwargs):
        row = await original(*args, **kwargs)
        acquired.set()
        await release.wait()
        return row
    monkeypatch.setattr(r1b, "lock_round", gated_player)
    async with SessionLocal() as player_db, SessionLocal() as admin_db:
        submitting = asyncio.create_task(r1b.submit(r1b.SubmitNumeric(answer="42"), user, player_db))
        await asyncio.wait_for(acquired.wait(), 3)
        revoking = asyncio.create_task(admin.revoke_all_team_sessions(
            team_id, admin.RevokeSessionsIn(reason="race test"), {"sub": "test-admin"}, admin_db))
        done, _ = await asyncio.wait({revoking}, timeout=.05)
        assert not done
        release.set()
        await asyncio.wait_for(asyncio.gather(submitting, revoking), 3)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Submission)) == 1
        assert not await db.scalar(select(PlayerDevice.active).where(PlayerDevice.session_id == user["sid"]))

    # Revocation owns the exclusive Round lock first: the queued submission
    # revalidates the row and writes nothing.
    team_id, user = await seed_active_submission_turn()
    monkeypatch.setattr(r1b, "lock_round", original)
    admin_original = admin.lock_round
    acquired, release = asyncio.Event(), asyncio.Event()
    async def gated_admin(*args, **kwargs):
        row = await admin_original(*args, **kwargs)
        acquired.set()
        await release.wait()
        return row
    monkeypatch.setattr(admin, "lock_round", gated_admin)
    async with SessionLocal() as admin_db, SessionLocal() as player_db:
        revoking = asyncio.create_task(admin.revoke_all_team_sessions(
            team_id, admin.RevokeSessionsIn(reason="race test"), {"sub": "test-admin"}, admin_db))
        await asyncio.wait_for(acquired.wait(), 3)
        submitting = asyncio.create_task(r1b.submit(r1b.SubmitNumeric(answer="42"), user, player_db))
        done, _ = await asyncio.wait({submitting}, timeout=.05)
        assert not done
        release.set()
        await asyncio.wait_for(revoking, 3)
        with pytest.raises(HTTPException) as rejected:
            await asyncio.wait_for(submitting, 3)
        assert rejected.value.status_code == 401
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Submission)) == 0


async def test_revocation_and_draft_save_have_deterministic_transaction_order(monkeypatch):
    require_disposable_test_database()
    from fastapi import HTTPException
    from sqlalchemy import func
    from app.api import admin, r1b
    from app.db import SessionLocal
    from app.models import R1BDraft, TeamRoundState

    team_id, user = await seed_active_submission_turn()
    async with SessionLocal() as db:
        turn_id = await db.scalar(select(TeamRoundState.current_turn_id).where(
            TeamRoundState.team_id == team_id))
    original = r1b.lock_round
    acquired, release = asyncio.Event(), asyncio.Event()
    async def gated_player(*args, **kwargs):
        row = await original(*args, **kwargs)
        acquired.set(); await release.wait(); return row
    monkeypatch.setattr(r1b, "lock_round", gated_player)
    async with SessionLocal() as player_db, SessionLocal() as admin_db:
        saving = asyncio.create_task(r1b.save_draft(
            r1b.SaveDraft(turn_id=turn_id, version=1, answer="42"), user, player_db))
        await asyncio.wait_for(acquired.wait(), 3)
        revoking = asyncio.create_task(admin.revoke_all_team_sessions(
            team_id, admin.RevokeSessionsIn(reason="draft race"), {"sub": "test-admin"}, admin_db))
        done, _ = await asyncio.wait({revoking}, timeout=.05); assert not done
        release.set(); await asyncio.wait_for(asyncio.gather(saving, revoking), 3)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(R1BDraft)) == 1

    team_id, user = await seed_active_submission_turn()
    async with SessionLocal() as db:
        turn_id = await db.scalar(select(TeamRoundState.current_turn_id).where(
            TeamRoundState.team_id == team_id))
    monkeypatch.setattr(r1b, "lock_round", original)
    admin_original = admin.lock_round
    acquired, release = asyncio.Event(), asyncio.Event()
    async def gated_admin(*args, **kwargs):
        row = await admin_original(*args, **kwargs)
        acquired.set(); await release.wait(); return row
    monkeypatch.setattr(admin, "lock_round", gated_admin)
    async with SessionLocal() as admin_db, SessionLocal() as player_db:
        revoking = asyncio.create_task(admin.revoke_all_team_sessions(
            team_id, admin.RevokeSessionsIn(reason="draft race"), {"sub": "test-admin"}, admin_db))
        await asyncio.wait_for(acquired.wait(), 3)
        saving = asyncio.create_task(r1b.save_draft(
            r1b.SaveDraft(turn_id=turn_id, version=1, answer="42"), user, player_db))
        done, _ = await asyncio.wait({saving}, timeout=.05); assert not done
        release.set(); await asyncio.wait_for(revoking, 3)
        with pytest.raises(HTTPException) as rejected:
            await asyncio.wait_for(saving, 3)
        assert rejected.value.status_code == 401
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(R1BDraft)) == 0
