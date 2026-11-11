"""Permanent deletion is tested only against guarded disposable PostgreSQL."""
import asyncio
import json
import os
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from tests.db_guard import require_disposable_test_database


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires disposable PostgreSQL"),
]


async def clear_data():
    from app.db import SessionLocal
    from app.models import (ActivationThrottle, Admin, AdminAction, LeaderboardSnapshot,
                            Question, Round, Team)
    async with SessionLocal() as db:
        # Team children use the deployed FK cascades. No truncation or schema reset.
        for model in (AdminAction, LeaderboardSnapshot, Team, Question, Round, Admin,
                      ActivationThrottle):
            await db.execute(delete(model))
        await db.commit()


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def ctx():
    require_disposable_test_database()
    from app.api.auth import new_activation_token
    from app.db import SessionLocal
    from app.main import app
    from app.models import Admin, Question, QuestionOption, Round, Team
    from app.security import create_token, hash_password_async
    from app.timing import utc_now
    await clear_data()
    activation, activation_hash = new_activation_token()
    password_hash = await hash_password_async("unused-password")
    async with SessionLocal() as db:
        admin = Admin(username="delete-admin", password_hash=password_hash)
        team = Team(code="DELETE", name="Unused Team", login_username="unused",
                    password_hash=password_hash, activation_token_hash=activation_hash,
                    activation_expires_at=utc_now() + timedelta(days=1))
        other = Team(code="KEEP", name="Other Team", login_username="keep",
                     password_hash=password_hash)
        db.add_all((admin, team, other, Round(code="R1A"), Round(code="R1B")))
        await db.flush()
        question = Question(round_code="R1A", position=1, answer_type="MCQ")
        db.add(question); await db.flush()
        option = QuestionOption(question_id=question.id, position=1, option_text="one")
        db.add(option); await db.flush()
        question.correct_option_id = option.id
        await db.commit()
        values = {"id": team.id, "other_id": other.id, "admin_id": admin.id,
                  "option_id": option.id, "activation": activation,
                  "headers": {"Authorization": "Bearer " + create_token(admin.username, "admin")},
                  "body": {"confirmation": team.name, "reason": "Duplicate unused registration"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        values["client"] = client
        yield values
    await clear_data()


async def remove(ctx, **kwargs):
    return await ctx["client"].request("DELETE", f'/api/admin/teams/{ctx["id"]}',
                                       headers=ctx["headers"], json=ctx["body"], **kwargs)


async def assert_preserved(ctx):
    from app.db import SessionLocal
    from app.models import AdminAction, Team
    async with SessionLocal() as db:
        assert await db.get(Team, ctx["id"]) is not None
        assert await db.scalar(select(func.count()).select_from(AdminAction).where(
            AdminAction.action == "DELETE_TEAM")) == 0


async def add_device(ctx, *, active, expired=False, other=False):
    from app.db import SessionLocal
    from app.models import PlayerDevice
    from app.timing import utc_now
    async with SessionLocal() as db:
        row = PlayerDevice(team_id=ctx["other_id"] if other else ctx["id"],
                           round_code="R1A", session_id="keep-session" if other else "unused-session",
                           active=active, expires_at=utc_now() + timedelta(minutes=-5 if expired else 5))
        db.add(row); await db.commit()


async def test_success_cascades_unused_sessions_preserves_audit_and_other_team(ctx):
    from app.db import SessionLocal
    from app.models import (AdminAction, LeaderboardSnapshot, PlayerDevice, RoundEnrollment,
                            ScoreEvent, Submission, Team, TeamRoundState)
    from app.security import create_token
    await add_device(ctx, active=False)
    await add_device(ctx, active=True, other=True)
    snapshot = json.dumps([{"team_id": ctx["other_id"], "team_code": "KEEP", "score": 0}])
    async with SessionLocal() as db:
        other = await db.get(Team, ctx["other_id"])
        other_before = (other.code, other.name, other.login_username, other.password_hash, other.enabled)
        db.add_all((
            RoundEnrollment(team_id=other.id, round_code="R1A"),
            TeamRoundState(team_id=other.id, round_code="R1B"),
            Submission(team_id=other.id, round_code="R1A", question_position=1, result="WRONG", points=-500),
            ScoreEvent(team_id=other.id, round_code="R1A", reason="test", points=-500),
            LeaderboardSnapshot(round_code="R1A", data_json=snapshot),
            AdminAction(admin_id=ctx["admin_id"], action="CREATE_TEAM", details=f'team_id={ctx["id"]}'),
        ))
        await db.commit()
    response = await remove(ctx)
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "team_id": ctx["id"]}
    async with SessionLocal() as db:
        assert await db.get(Team, ctx["id"]) is None
        assert await db.scalar(select(func.count()).select_from(PlayerDevice).where(
            PlayerDevice.team_id == ctx["id"])) == 0
        other = await db.get(Team, ctx["other_id"])
        assert (other.code, other.name, other.login_username, other.password_hash, other.enabled) == other_before
        assert await db.scalar(select(PlayerDevice.active).where(PlayerDevice.team_id == other.id)) is True
        for model in (Submission, ScoreEvent, RoundEnrollment, TeamRoundState):
            assert await db.scalar(select(func.count()).select_from(model).where(model.team_id == other.id)) == 1
        assert await db.scalar(select(LeaderboardSnapshot.data_json)) == snapshot
        audit = await db.scalar(select(AdminAction).where(AdminAction.action == "DELETE_TEAM"))
        assert audit.admin_id == ctx["admin_id"]
        details = json.loads(audit.details)
        assert details.items() >= {"team_id": ctx["id"], "team_code": "DELETE", "team_name": "Unused Team",
                                   "login_username": "unused", "reason": ctx["body"]["reason"]}.items()
        assert "password" not in audit.details and ctx["activation"] not in audit.details
        assert await db.scalar(select(func.count()).select_from(AdminAction)) == 2
    # Removed password/activation credentials and previously issued JWTs cannot authenticate.
    assert (await ctx["client"].post("/api/auth/team/login", json={
        "username": "unused", "password": "unused-password"})).status_code == 401
    assert (await ctx["client"].post("/api/auth/team/set-password", json={
        "username": "unused", "activation_token": ctx["activation"], "new_password": "another-password"
    })).status_code == 400
    token = create_token("DELETE", "team", ctx["id"], "unused-session", "R1A", 1)
    headers = {"Authorization": "Bearer " + token}
    for endpoint, method in (("me", "GET"), ("heartbeat", "POST")):
        assert (await ctx["client"].request(method, "/api/auth/team/" + endpoint, headers=headers)).status_code == 401
    keep_token = create_token("KEEP", "team", ctx["other_id"], "keep-session", "R1A", 1)
    assert (await ctx["client"].get("/api/auth/team/me", headers={
        "Authorization": "Bearer " + keep_token})).status_code == 200


@pytest.mark.parametrize("credentials", ["missing", "team", "disabled", "nonexistent"])
async def test_delete_requires_current_enabled_admin(ctx, credentials):
    from app.db import SessionLocal
    from app.models import Admin
    from app.security import create_token
    if credentials == "missing":
        ctx["headers"] = {}
    elif credentials == "team":
        ctx["headers"] = {"Authorization": "Bearer " + create_token("DELETE", "team", ctx["id"], "sid", "R1A", 1)}
    elif credentials == "nonexistent":
        ctx["headers"] = {"Authorization": "Bearer " + create_token("unknown-admin", "admin")}
    else:
        async with SessionLocal() as db:
            admin = await db.get(Admin, ctx["admin_id"]); admin.enabled = False
            await db.commit()
    assert (await remove(ctx)).status_code in (401, 403)
    await assert_preserved(ctx)


@pytest.mark.parametrize("body", [
    {"confirmation": "unused team", "reason": "duplicate"},
    {"confirmation": "Unused Team ", "reason": "duplicate"},
    {"confirmation": "DELETE", "reason": "duplicate"},
    {"confirmation": "Unused Team", "reason": "   "},
    {"confirmation": "Unused Team"},
    {"reason": "duplicate"},
])
async def test_exact_name_and_nonblank_reason_required(ctx, body):
    ctx["body"] = body
    assert (await remove(ctx)).status_code == 422
    await assert_preserved(ctx)


@pytest.mark.parametrize("expired", [False, True])
async def test_active_sessions_reject_even_renewable_expired_leases(ctx, expired):
    await add_device(ctx, active=True, expired=expired)
    response = await remove(ctx)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TEAM_HAS_ACTIVE_SESSIONS"
    await assert_preserved(ctx)


@pytest.mark.parametrize("record", ["enrollment", "waiting_relay", "active_relay", "finished_relay",
                                   "submission", "score_zero", "score_negative", "draft",
                                   "r1a_pending", "r1a_rejected", "r1b_pending", "r1b_rejected"])
async def test_every_competition_record_blocks_deletion(ctx, record):
    from app.db import SessionLocal
    from app.models import (R1AReceipt, R1BDraft, R1BReceipt, RoundEnrollment,
                            ScoreEvent, Submission, TeamRoundState)
    from app.timing import utc_now
    now = utc_now(); team_id = ctx["id"]
    if record == "enrollment":
        row = RoundEnrollment(team_id=team_id, round_code="R1A")
    elif record.endswith("relay"):
        row = TeamRoundState(team_id=team_id, round_code="R1B", turn_status={
            "waiting_relay": "WAITING", "active_relay": "ACTIVE", "finished_relay": "FINISHED"}[record])
    elif record == "submission":
        row = Submission(team_id=team_id, round_code="R1A", question_position=1, result="UNANSWERED", points=0)
    elif record.startswith("score"):
        row = ScoreEvent(team_id=team_id, round_code="R1B", reason="test", points=0 if record == "score_zero" else -500)
    elif record == "draft":
        row = R1BDraft(team_id=team_id, question_position=1, player_number=1,
                       turn_id="turn", version=1, answer_text="", accepted_at=now)
    elif record.startswith("r1a"):
        row = R1AReceipt(team_id=team_id, session_id="old", option_id=ctx["option_id"],
                         received_at=now, status="PENDING" if record.endswith("pending") else "REJECTED")
    else:
        row = R1BReceipt(team_id=team_id, session_id="old", turn_id="turn", question_position=1,
                         player_number=1, action="SKIP", received_at=now, turn_ready_at=now,
                         team_deadline=now, status="PENDING" if record.endswith("pending") else "REJECTED")
    async with SessionLocal() as db:
        db.add(row); await db.commit()
    response = await remove(ctx)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TEAM_HAS_COMPETITION_HISTORY"
    await assert_preserved(ctx)
    async with SessionLocal() as db:
        assert await db.get(type(row), row.id) is not None


@pytest.mark.parametrize("kind", ["legacy", "R1A_QUESTION_REVEAL_V1", "R1B_TURN_REVEAL_V1",
                                 "EXPLICIT_FINAL_PUBLICATION_V1", "COMBINED_R1_FINAL_V1",
                                 "qualification", "cutoff_tie", "old_snapshot"])
async def test_publication_rows_and_qualification_references_block(ctx, kind):
    from app.db import SessionLocal
    from app.models import LeaderboardSnapshot
    row = {"team_id": ctx["id"], "score": 0}
    payload = [row] if kind == "legacy" else {"kind": kind, "rows": [row]}
    if kind in ("qualification", "cutoff_tie"):
        payload = {"kind": "COMBINED_R1_FINAL_V1", "rows": [], "qualification": {
            "team_ids" if kind == "qualification" else "cutoff_tie_team_ids": [ctx["id"]]}}
    async with SessionLocal() as db:
        if kind == "old_snapshot":
            payload = {"kind": "EXPLICIT_FINAL_PUBLICATION_V1", "rows": [row]}
            db.add(LeaderboardSnapshot(round_code="R1A", data_json=json.dumps(payload)))
            await db.flush()
            # Check every snapshot, not only the newest publication.
            payload = {"kind": "EXPLICIT_FINAL_PUBLICATION_V1", "rows": []}
        db.add(LeaderboardSnapshot(round_code="R1" if kind in ("COMBINED_R1_FINAL_V1", "qualification", "cutoff_tie") else "R1A",
                                   data_json=json.dumps(payload)))
        await db.commit()
    response = await remove(ctx)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TEAM_HAS_PUBLISHED_RESULTS"
    await assert_preserved(ctx)


@pytest.mark.parametrize("raw", ["bad json", "null", '{"kind":"UNKNOWN","rows":[]}',
                                '[{"team_name":"Unknown identity"}]'])
async def test_unverifiable_snapshot_fails_closed(ctx, raw):
    from app.db import SessionLocal
    from app.models import LeaderboardSnapshot
    async with SessionLocal() as db:
        db.add(LeaderboardSnapshot(round_code="R1A", data_json=raw)); await db.commit()
    response = await remove(ctx)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TEAM_HISTORY_UNVERIFIABLE"
    await assert_preserved(ctx)


@pytest.mark.parametrize("predates_reset", [True, False])
async def test_recorded_reset_does_not_erase_historical_deletion_guard(ctx, predates_reset):
    from app.db import SessionLocal
    from app.models import AdminAction, Team
    async with SessionLocal() as db:
        team = await db.get(Team, ctx["id"])
        db.add(AdminAction(action="RESET_ROUND_ONE", created_at=team.created_at + timedelta(
            seconds=1 if predates_reset else -1), details='{"affected_records":{}}'))
        await db.commit()
    response = await remove(ctx)
    assert response.status_code == (409 if predates_reset else 200)
    if predates_reset:
        assert response.json()["detail"]["code"] == "TEAM_HISTORY_UNVERIFIABLE"
        await assert_preserved(ctx)


async def test_disabled_unused_team_is_deletable(ctx):
    from app.db import SessionLocal
    from app.models import Team
    async with SessionLocal() as db:
        team = await db.get(Team, ctx["id"]); team.enabled = False
        await db.commit()
    assert (await remove(ctx)).status_code == 200


async def test_unactivated_team_credentials_are_removed(ctx):
    from app.db import SessionLocal
    from app.models import Team
    async with SessionLocal() as db:
        team = await db.get(Team, ctx["id"]); team.password_hash = None
        await db.commit()
    assert (await remove(ctx)).status_code == 200
    response = await ctx["client"].post("/api/auth/team/set-password", json={
        "username": "unused", "activation_token": ctx["activation"], "new_password": "new-password"})
    assert response.status_code == 400


async def test_audit_failure_rolls_back_team_credentials_and_cascade(ctx):
    from app.db import SessionLocal
    from app.models import PlayerDevice, Team
    from sqlalchemy.exc import DBAPIError
    await add_device(ctx, active=False)
    async with SessionLocal() as db:
        await db.execute(text("""CREATE FUNCTION reject_team_delete_audit() RETURNS trigger AS $$
            BEGIN IF NEW.action='DELETE_TEAM' THEN RAISE EXCEPTION 'forced audit failure'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql"""))
        await db.execute(text("""CREATE TRIGGER reject_team_delete_audit BEFORE INSERT ON admin_actions
            FOR EACH ROW EXECUTE FUNCTION reject_team_delete_audit()"""))
        await db.commit()
    try:
        with pytest.raises(DBAPIError):
            await remove(ctx)
        await assert_preserved(ctx)
        async with SessionLocal() as db:
            team = await db.get(Team, ctx["id"])
            assert team.password_hash and team.activation_token_hash
            assert await db.scalar(select(PlayerDevice.id).where(PlayerDevice.team_id == team.id))
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DROP TRIGGER reject_team_delete_audit ON admin_actions"))
            await db.execute(text("DROP FUNCTION reject_team_delete_audit()")); await db.commit()


async def assert_waiting(task):
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done, "operation bypassed the held PostgreSQL lock"


def gate_deletion(monkeypatch):
    from app.api import admin
    original = admin.lock_unused_team
    acquired, release = asyncio.Event(), asyncio.Event()

    async def gated(*args):
        team = await original(*args)
        acquired.set()
        await release.wait()
        return team

    monkeypatch.setattr(admin, "lock_unused_team", gated)
    return acquired, release


async def test_concurrent_deletions_write_one_audit(ctx, monkeypatch):
    from app.db import SessionLocal
    from app.models import AdminAction
    acquired, release = gate_deletion(monkeypatch)
    first = asyncio.create_task(remove(ctx))
    await asyncio.wait_for(acquired.wait(), 3)
    second = asyncio.create_task(remove(ctx))
    try:
        await assert_waiting(second)
    finally:
        release.set()
    responses = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert [r.status_code for r in responses] == [200, 404]
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(AdminAction).where(AdminAction.action == "DELETE_TEAM")) == 1


@pytest.mark.parametrize("login_first", [False, True])
async def test_login_and_deletion_serialize(ctx, monkeypatch, login_first):
    from app.api import auth
    async def login():
        return await ctx["client"].post("/api/auth/team/login", json={"username": "unused", "password": "unused-password"})
    if login_first:
        original = auth.lock_login_round
        acquired, release = asyncio.Event(), asyncio.Event()
        async def gated(db):
            row = await original(db); acquired.set(); await release.wait(); return row
        monkeypatch.setattr(auth, "lock_login_round", gated)
        first = asyncio.create_task(login())
    else:
        acquired, release = gate_deletion(monkeypatch)
        first = asyncio.create_task(remove(ctx))
    await asyncio.wait_for(acquired.wait(), 3)
    second = asyncio.create_task(remove(ctx) if login_first else login())
    try:
        await assert_waiting(second)
    finally:
        release.set()
    first_result, second_result = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert first_result.status_code == 200
    assert second_result.status_code == (409 if login_first else 401)
    if login_first:
        await assert_preserved(ctx)


@pytest.mark.parametrize("publication_first", [False, True])
async def test_publication_and_deletion_serialize(ctx, monkeypatch, publication_first):
    from app.api import admin
    from app.db import SessionLocal
    from app.models import LeaderboardSnapshot, Round
    async with SessionLocal() as db:
        round_obj = await db.scalar(select(Round).where(Round.code == "R1A"))
        round_obj.status = "ENDED"; await db.commit()
    async def publish():
        return await ctx["client"].post("/api/admin/leaderboard/publish", headers=ctx["headers"], json={"round_code": "R1A"})
    if publication_first:
        original = admin.audit
        acquired, release = asyncio.Event(), asyncio.Event()
        async def gated(db, user, action, details):
            await original(db, user, action, details)
            if action == "PUBLISH_LEADERBOARD":
                acquired.set(); await release.wait()
        monkeypatch.setattr(admin, "audit", gated)
        first = asyncio.create_task(publish())
    else:
        acquired, release = gate_deletion(monkeypatch)
        first = asyncio.create_task(remove(ctx))
    await asyncio.wait_for(acquired.wait(), 3)
    second = asyncio.create_task(remove(ctx) if publication_first else publish())
    try:
        await assert_waiting(second)
    finally:
        release.set()
    first_result, second_result = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert first_result.status_code == 200
    assert second_result.status_code == (409 if publication_first else 200)
    async with SessionLocal() as db:
        rows = json.loads(await db.scalar(select(LeaderboardSnapshot.data_json)))["rows"]
        assert (ctx["id"] in [r["team_id"] for r in rows]) is publication_first


@pytest.mark.parametrize("writer_first", [False, True])
async def test_foreign_key_insertion_and_deletion_serialize(ctx, monkeypatch, writer_first):
    from app.db import SessionLocal
    from app.models import ScoreEvent
    async with SessionLocal() as writer:
        if writer_first:
            writer.add(ScoreEvent(team_id=ctx["id"], round_code="R1A", reason="race", points=0))
            await writer.flush()  # PostgreSQL holds KEY SHARE on the referenced team.
            removal = asyncio.create_task(remove(ctx))
            await assert_waiting(removal)
            await writer.commit()
            response = await asyncio.wait_for(removal, 5)
            assert response.status_code == 409
            await assert_preserved(ctx)
        else:
            acquired, release = gate_deletion(monkeypatch)
            removal = asyncio.create_task(remove(ctx))
            await asyncio.wait_for(acquired.wait(), 3)
            writer.add(ScoreEvent(team_id=ctx["id"], round_code="R1A", reason="race", points=0))
            insertion = asyncio.create_task(writer.flush())
            try:
                await assert_waiting(insertion)
            finally:
                release.set()
            assert (await asyncio.wait_for(removal, 5)).status_code == 200
            with pytest.raises(IntegrityError):
                await asyncio.wait_for(insertion, 5)
            await writer.rollback()


@pytest.mark.parametrize("start_first", [False, True])
async def test_enrollment_and_deletion_serialize(ctx, monkeypatch, start_first):
    from app.api import admin
    from app.competition_rules import configured_rules
    from app.db import SessionLocal
    from app.models import Question, QuestionOption, RoundEnrollment
    async with SessionLocal() as db:
        for position in range(2, configured_rules().r1a.question_count + 1):
            question = Question(round_code="R1A", position=position, answer_type="MCQ")
            db.add(question); await db.flush()
            option = QuestionOption(question_id=question.id, position=1, option_text="one")
            db.add(option); await db.flush(); question.correct_option_id = option.id
        await db.commit()
    async def start():
        return await ctx["client"].post("/api/admin/rounds/start", headers=ctx["headers"], json={"round_code": "R1A"})
    if start_first:
        original = admin.audit
        acquired, release = asyncio.Event(), asyncio.Event()
        async def gated(db, user, action, details):
            await original(db, user, action, details)
            if action == "START_ROUND":
                acquired.set(); await release.wait()
        monkeypatch.setattr(admin, "audit", gated)
        first = asyncio.create_task(start())
    else:
        acquired, release = gate_deletion(monkeypatch)
        first = asyncio.create_task(remove(ctx))
    await asyncio.wait_for(acquired.wait(), 3)
    second = asyncio.create_task(remove(ctx) if start_first else start())
    try:
        await assert_waiting(second)
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert results[0].status_code == 200, results[0].text
    assert results[1].status_code == (409 if start_first else 200), results[1].text
    async with SessionLocal() as db:
        enrolled = (await db.scalars(select(RoundEnrollment.team_id))).all()
        assert (ctx["id"] in enrolled) is start_first
        assert ctx["other_id"] in enrolled


@pytest.mark.parametrize("activation_first", [False, True])
async def test_password_activation_and_deletion_serialize_without_lock_inversion(ctx, monkeypatch, activation_first):
    from app.api import auth
    from app.db import SessionLocal
    from app.models import Team
    async with SessionLocal() as db:
        team = await db.get(Team, ctx["id"]); team.password_hash = None
        await db.commit()
    async def activate():
        return await ctx["client"].post("/api/auth/team/set-password", json={
            "username": "unused", "activation_token": ctx["activation"], "new_password": "new-password"})
    if activation_first:
        original = auth.hash_password_async
        acquired, release = asyncio.Event(), asyncio.Event()
        async def gated(password):
            acquired.set(); await release.wait(); return await original(password)
        monkeypatch.setattr(auth, "hash_password_async", gated)
        first = asyncio.create_task(activate())
    else:
        acquired, release = gate_deletion(monkeypatch)
        first = asyncio.create_task(remove(ctx))
    await asyncio.wait_for(acquired.wait(), 3)
    second = asyncio.create_task(remove(ctx) if activation_first else activate())
    try:
        await assert_waiting(second)
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert results[0].status_code == 200
    assert results[1].status_code == (200 if activation_first else 400)
    async with SessionLocal() as db:
        assert await db.get(Team, ctx["id"]) is None
