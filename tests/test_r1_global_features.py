"""Global R1 rehearsal reset and immutable combined results on disposable PG.

Every test uses a new schema in the guarded local test database. No application
URL, deployed database, or production credentials are used by this module.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.db_guard import require_disposable_test_database


pytestmark = [
    pytest.mark.database,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires guarded disposable PostgreSQL"),
]

RESET_BODY = {"reason": "another disposable rehearsal", "confirmation": "RESET ROUND 1"}


@pytest_asyncio.fixture(loop_scope="session")
async def ctx(monkeypatch):
    require_disposable_test_database()
    from app.db import Base, get_db
    from app.main import app
    from app.models import Admin, Round
    from app.security import create_token
    from app.timing import utc_now

    schema = "r1_global_" + uuid4().hex
    root = create_async_engine(os.environ["DATABASE_URL"])
    async with root.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        os.environ["DATABASE_URL"],
        connect_args={"server_settings": {"search_path": schema}},
        pool_size=12, max_overflow=12,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        path = Path(__file__).parents[1] / "migrations/versions/add_combined_r1_publication.py"
        spec = importlib.util.spec_from_file_location("combined_test_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        await connection.execute(text(migration.IMMUTABILITY_FUNCTION_SQL))
        await connection.execute(text(migration.IMMUTABILITY_TRIGGER_SQL))
    local = async_sessionmaker(engine, expire_on_commit=False)
    async with local() as db:
        db.add(Admin(username="r1-global-admin", password_hash="preserved-admin-hash"))
        db.add_all([Round(code="R1A", status="DRAFT"), Round(code="R1B", status="DRAFT")])
        await db.commit()

    async def session():
        async with local() as db:
            yield db

    app.dependency_overrides[get_db] = session
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        yield {
            "local": local, "engine": engine, "client": client, "schema": schema,
            "headers": {"Authorization": "Bearer " + create_token("r1-global-admin", "admin")},
            "now": utc_now(), "monkeypatch": monkeypatch,
        }
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
        async with root.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await root.dispose()


async def authorize_rehearsal(ctx):
    from app.api import admin
    from app.models import RehearsalAuthorization

    token = "disposable-schema-reset-token-at-least-32-characters"
    async with ctx["local"]() as db:
        name, oid = (await db.execute(text(
            "SELECT current_database(), oid FROM pg_database WHERE datname=current_database()"
        ))).one()
        db.add(RehearsalAuthorization(
            id=1, database_name=name, database_oid=oid,
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        ))
        await db.commit()
    for key, value in {
        "environment": "rehearsal", "r1_reset_enabled": True,
        "r1_rehearsal_token": token, "r1_reset_database_name": name,
    }.items():
        ctx["monkeypatch"].setattr(admin.settings, key, value)


async def set_round_status(ctx, a="ENDED", b="ENDED"):
    from app.models import Round
    async with ctx["local"]() as db:
        for row in (await db.scalars(select(Round))).all():
            row.status = a if row.code == "R1A" else b
            row.ended_at = ctx["now"] if row.status == "ENDED" else None
        await db.commit()


async def seed_combined(ctx):
    """Eligibility is participation, independent of enabled flags or outcomes."""
    from app.models import RoundEnrollment, ScoreEvent, Submission, Team, TeamRoundState
    await set_round_status(ctx)
    async with ctx["local"]() as db:
        teams = {
            code: Team(code=code, name="Team " + code, login_username="global-" + code,
                       enabled=code != "D", password_hash="preserved-" + code)
            for code in "ABCDEF"
        }
        db.add_all(teams.values())
        await db.flush()
        for code in "ABD":
            db.add(RoundEnrollment(team_id=teams[code].id, round_code="R1A"))
        # C has relay participation, but never submitted. E has only an outcome.
        db.add(TeamRoundState(team_id=teams["C"].id, round_code="R1B", turn_status="FINISHED"))
        submission = Submission(team_id=teams["E"].id, round_code="R1B", question_position=1,
                                player_number=1, answer_text="wrong", result="WRONG", points=-1500)
        db.add(submission)
        await db.flush()
        for code, round_code, points in [
            ("A", "R1A", 500), ("A", "R1A", 500), ("A", "R1B", 4000),
            ("B", "R1A", 1000), ("B", "R1B", -1500), ("D", "R1A", -500),
            ("E", "R1B", -1500), ("A", "R2", 999999),
        ]:
            db.add(ScoreEvent(team_id=teams[code].id, round_code=round_code, points=points,
                              reason="fixture", submission_id=submission.id if code == "E" else None))
        await db.commit()
        return {code: team.id for code, team in teams.items()}


async def preview(ctx):
    return await ctx["client"].get("/api/admin/leaderboard/combined", headers=ctx["headers"])


async def publish(ctx):
    return await ctx["client"].post("/api/admin/leaderboard/publish", headers=ctx["headers"],
                                    json={"round_code": "R1"})


async def test_combined_positive_zero_negative_missing_submissions_and_disabled_participants(ctx):
    ids = await seed_combined(ctx)
    response = await preview(ctx)
    assert response.status_code == 200, response.text
    board = response.json()
    assert board["ready"] is True
    assert [(row["team_code"], row["rank"], row["r1a_score"], row["r1b_score"], row["score"])
            for row in board["data"]] == [
        ("A", 1, 1000, 4000, 5000), ("C", 2, 0, 0, 0),
        ("B", 3, 1000, -1500, -500), ("D", 3, -500, 0, -500),
        ("E", 5, 0, -1500, -1500),
    ]
    assert ids["F"] not in [row["team_id"] for row in board["data"]]
    assert (await preview(ctx)).json()["data"] == board["data"]
    public = await ctx["client"].get("/api/leaderboard/final")
    assert public.status_code == 200 and public.json()["published"] is False
    assert public.json()["data"] == []
    assert board["qualification"]["team_ids"] == []


@pytest.mark.parametrize("a,b", [("DRAFT", "DRAFT"), ("LIVE", "ENDED"),
                                   ("ENDED", "PAUSED"), ("ENDED", "LIVE")])
async def test_combined_publication_blocked_until_both_officially_ended(ctx, a, b):
    await seed_combined(ctx)
    await set_round_status(ctx, a, b)
    response = await publish(ctx)
    assert response.status_code == 409, response.text
    board = (await preview(ctx)).json()
    assert board["ready"] is False and board["data"] == []
    assert (await ctx["client"].get("/api/leaderboard/final")).json()["published"] is False


async def test_combined_publication_is_immutable_idempotent_audited_and_public(ctx):
    from app.models import AdminAction, LeaderboardSnapshot, ScoreEvent, Team
    await seed_combined(ctx)
    first = await publish(ctx)
    assert first.status_code == 200, first.text
    original = (await ctx["client"].get("/api/leaderboard/final")).json()
    assert original["published"] is True and len(original["data"]) == 5
    assert original["qualification"]["status"] == "READY"
    assert original["qualification"]["team_ids"] == [row["team_id"] for row in original["data"]]
    async with ctx["local"]() as db:
        snapshot = await db.scalar(select(LeaderboardSnapshot).where(LeaderboardSnapshot.round_code == "R1"))
        frozen = snapshot.data_json
        team = await db.scalar(select(Team).where(Team.code == "A"))
        team.name = "Later rename"
        db.add(ScoreEvent(team_id=team.id, round_code="R1A", reason="later source adjustment", points=-99999))
        await db.commit()
    repeated = await publish(ctx)
    assert repeated.status_code == 200 and repeated.json()["publication_id"] == first.json()["publication_id"]
    assert repeated.json()["already_published"] is True
    assert (await ctx["client"].get("/api/leaderboard/final")).json() == original
    async with ctx["local"]() as db:
        assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot).where(
            LeaderboardSnapshot.round_code == "R1")) == 1
        assert await db.scalar(select(LeaderboardSnapshot.data_json).where(
            LeaderboardSnapshot.round_code == "R1")) == frozen
        assert await db.scalar(select(func.count()).select_from(AdminAction).where(
            AdminAction.action == "PUBLISH_COMBINED_R1_LEADERBOARD")) == 1


async def test_concurrent_combined_publications_have_one_frozen_publication(ctx):
    from app.models import LeaderboardSnapshot
    await seed_combined(ctx)
    responses = await asyncio.wait_for(asyncio.gather(*(publish(ctx) for _ in range(6))), 5)
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert len({response.json()["publication_id"] for response in responses}) == 1
    assert sum(not response.json().get("already_published", False) for response in responses) == 1
    async with ctx["local"]() as db:
        assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot).where(
            LeaderboardSnapshot.round_code == "R1")) == 1


async def test_cutoff_tie_is_stable_and_never_invents_r2_qualifiers(ctx):
    from app.models import RoundEnrollment, ScoreEvent, Team
    await set_round_status(ctx)
    async with ctx["local"]() as db:
        for number in reversed(range(1, 19)):
            team = Team(code=f"T{number:02}", name=f"Team {number}", login_username=f"cutoff-{number}")
            db.add(team)
            await db.flush()
            db.add(RoundEnrollment(team_id=team.id, round_code="R1A"))
            db.add(ScoreEvent(team_id=team.id, round_code="R1A", reason="cutoff fixture",
                              points=100 - number if number < 16 else 0))
        await db.commit()
    assert (await publish(ctx)).status_code == 200
    board = (await ctx["client"].get("/api/leaderboard/final")).json()
    assert len(board["data"]) == 18
    assert [row["team_code"] for row in board["data"][-3:]] == ["T16", "T17", "T18"]
    assert [row["rank"] for row in board["data"][-3:]] == [16, 16, 16]
    assert board["qualification"]["status"] == "BLOCKED_CUTOFF_TIE"
    assert board["qualification"]["team_ids"] == []
    assert set(board["qualification"]["cutoff_tie_team_ids"]) == {
        row["team_id"] for row in board["data"][-3:]
    }
    assert "tie" in json.dumps(board).lower()
    rejected = await ctx["client"].get("/api/admin/leaderboard/combined/qualifiers", headers=ctx["headers"])
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "R2_QUALIFICATION_TIE_UNRESOLVED"


async def test_combined_admin_and_public_access_boundaries(ctx):
    from app.models import PlayerDevice
    from app.security import create_token
    ids = await seed_combined(ctx)
    async with ctx["local"]() as db:
        db.add(PlayerDevice(team_id=ids["A"], player_number=1, round_code="R1B",
                            session_id="global-player-session", active=True,
                            expires_at=ctx["now"] + timedelta(minutes=10)))
        await db.commit()
    player_headers = {"Authorization": "Bearer " + create_token(
        "A", "team", ids["A"], "global-player-session", "R1B", 1)}
    for method, path, body in [
        ("GET", "/api/admin/leaderboard/combined", None),
        ("POST", "/api/admin/leaderboard/publish", {"round_code": "R1"}),
        ("POST", "/api/admin/rounds/reset", RESET_BODY),
    ]:
        assert (await ctx["client"].request(method, path, json=body)).status_code in (401, 403)
        assert (await ctx["client"].request(method, path, headers=player_headers, json=body)).status_code == 403
    assert (await ctx["client"].post("/api/leaderboard/final", json={})).status_code == 405
    assert (await ctx["client"].get("/api/leaderboard/final", headers=player_headers)).json()["published"] is False
    assert (await publish(ctx)).status_code == 200
    participant = await ctx["client"].get("/api/leaderboard/final", headers=player_headers)
    assert participant.status_code == 200 and participant.json()["published"] is True
    assert "password" not in participant.text and "answer_text" not in participant.text


async def install_failure(ctx, action):
    async with ctx["local"]() as db:
        await db.execute(text("""
            CREATE FUNCTION reject_global_audit() RETURNS trigger AS $$
            BEGIN
                IF NEW.action = TG_ARGV[0] THEN
                    RAISE EXCEPTION 'forced global transaction failure';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql
        """))
        await db.execute(text(f"""CREATE TRIGGER reject_global_audit BEFORE INSERT ON admin_actions
            FOR EACH ROW EXECUTE FUNCTION reject_global_audit('{action}')"""))
        await db.commit()


async def remove_failure(ctx):
    async with ctx["local"]() as db:
        await db.execute(text("DROP TRIGGER reject_global_audit ON admin_actions"))
        await db.execute(text("DROP FUNCTION reject_global_audit()"))
        await db.commit()


async def test_combined_failed_transaction_never_publishes_and_can_retry(ctx):
    from app.models import LeaderboardSnapshot
    await seed_combined(ctx)
    await install_failure(ctx, "PUBLISH_COMBINED_R1_LEADERBOARD")
    with pytest.raises(Exception, match="forced global transaction failure"):
        await publish(ctx)
    async with ctx["local"]() as db:
        assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot)) == 0
    assert (await ctx["client"].get("/api/leaderboard/final")).json()["published"] is False
    await remove_failure(ctx)
    assert (await publish(ctx)).status_code == 200


async def test_published_combined_database_row_cannot_be_updated(ctx):
    from sqlalchemy.exc import IntegrityError
    await seed_combined(ctx)
    assert (await publish(ctx)).status_code == 200
    original = (await ctx["client"].get("/api/leaderboard/final")).json()
    async with ctx["local"]() as db:
        with pytest.raises(IntegrityError, match="immutable"):
            await db.execute(text("UPDATE leaderboard_snapshots SET data_json='{}' WHERE round_code='R1'"))
            await db.commit()
        await db.rollback()
    assert (await ctx["client"].get("/api/leaderboard/final")).json() == original


async def test_qualifier_endpoint_uses_only_frozen_published_combined_order(ctx):
    await seed_combined(ctx)
    path = "/api/admin/leaderboard/combined/qualifiers"
    assert (await ctx["client"].get(path, headers=ctx["headers"])).status_code == 409
    assert (await publish(ctx)).status_code == 200
    response = await ctx["client"].get(path, headers=ctx["headers"])
    assert response.status_code == 200
    qualifiers = response.json()
    assert qualifiers["source"] == "R1_COMBINED_PUBLICATION"
    assert qualifiers["team_ids"] == [row["team_id"] for row in qualifiers["data"]]
    assert (await ctx["client"].get(path)).status_code in (401, 403)


async def test_pending_admissions_block_combined_preview_and_publication(ctx):
    from app.models import R1BReceipt
    ids = await seed_combined(ctx)
    async with ctx["local"]() as db:
        db.add(R1BReceipt(team_id=ids["A"], session_id="pending-session", turn_id=str(uuid4()),
                        question_position=1, player_number=1, action="SUBMIT", answer_text="42",
                        received_at=ctx["now"], turn_ready_at=ctx["now"],
                        team_deadline=ctx["now"] + timedelta(minutes=1), status="PENDING"))
        await db.commit()
    response = await publish(ctx)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "COMBINED_RESULTS_PENDING"
    board = (await preview(ctx)).json()
    assert board["ready"] is False and board["data"] == []
    assert board["blocked_reason"] == "COMBINED_RESULTS_PENDING"
    assert (await ctx["client"].get("/api/leaderboard/final")).json()["published"] is False


async def seed_reset_progress(ctx):
    from app.models import (AdminAction, LeaderboardSnapshot, PlayerDevice, Question,
                            QuestionOption, R1AReceipt, R1BReceipt, R1BDraft,
                            RoundEnrollment, ScoreEvent, Submission, Team, TeamRoundState)
    from app.question_reveal import FINAL_KIND
    await seed_combined(ctx)
    assert (await publish(ctx)).status_code == 200
    async with ctx["local"]() as db:
        team = await db.scalar(select(Team).where(Team.code == "A"))
        team.activation_token_hash = "a" * 64
        team.activation_expires_at = ctx["now"] + timedelta(days=1)
        question = Question(round_code="R1A", position=1, answer_type="MCQ",
                            question_text="Preserved question", question_image_url="/media/preserved.png")
        db.add(question)
        await db.flush()
        option = QuestionOption(question_id=question.id, position=1, option_text="Preserved option",
                                image_url="/media/preserved-option.png")
        db.add(option)
        await db.flush()
        question.correct_option_id = option.id
        db.add(Question(round_code="R1B", position=1, answer_type="NUMERIC",
                        correct_numeric=Decimal("42"), tolerance_abs=Decimal("0.01")))
        turn_id = str(uuid4())
        db.add_all([
            R1AReceipt(team_id=team.id, session_id="old-a", option_id=option.id,
                        received_at=ctx["now"], status="PENDING", question_position=1),
            R1BReceipt(team_id=team.id, session_id="old-b", turn_id=turn_id,
                        question_position=1, player_number=1, action="SUBMIT", answer_text="42",
                        received_at=ctx["now"], turn_ready_at=ctx["now"],
                        team_deadline=ctx["now"] + timedelta(minutes=1), status="PENDING"),
            R1BDraft(team_id=team.id, question_position=1, player_number=1, turn_id=turn_id,
                      version=1, answer_text="42", accepted_at=ctx["now"]),
            PlayerDevice(team_id=team.id, player_number=1, round_code="R1A", session_id="old-a",
                          active=True, expires_at=ctx["now"] + timedelta(minutes=10)),
            PlayerDevice(team_id=team.id, player_number=1, round_code="R1B", session_id="old-b",
                          active=True, expires_at=ctx["now"] + timedelta(minutes=10)),
            TeamRoundState(team_id=team.id, round_code="R1B", current_player=2,
                            current_question_position=2, turn_status="WAITING",
                            team_started_at=ctx["now"], remaining_time_ms=12345),
            LeaderboardSnapshot(round_code="R1A", data_json=json.dumps({"kind": FINAL_KIND, "rows": []})),
            LeaderboardSnapshot(round_code="R1B", data_json=json.dumps({"kind": FINAL_KIND, "rows": []})),
            LeaderboardSnapshot(round_code="R2", data_json="[]"),
            Submission(team_id=team.id, round_code="R2", question_position=1, result="CORRECT", points=777),
            TeamRoundState(team_id=team.id, round_code="R2", turn_status="FINISHED"),
            AdminAction(action="HISTORICAL_SENTINEL", details="preserve history"),
        ])
        await db.commit()


async def test_reset_preserves_seeded_identity_content_and_unrelated_round_history(ctx):
    from app.models import (Admin, AdminAction, LeaderboardSnapshot, PlayerDevice, Question,
                            QuestionOption, R1AReceipt, R1BReceipt, R1BDraft, Round,
                            RoundEnrollment, ScoreEvent, Submission, Team, TeamRoundState)
    await authorize_rehearsal(ctx)
    await seed_reset_progress(ctx)
    async with ctx["local"]() as db:
        before = {
            model.__tablename__: [dict(row) for row in (await db.execute(select(model.__table__))).mappings()]
            for model in (Admin, Team, Question, QuestionOption)
        }
    response = await ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    async with ctx["local"]() as db:
        for model in (Admin, Team, Question, QuestionOption):
            assert [dict(row) for row in (await db.execute(select(model.__table__))).mappings()] == before[model.__tablename__]
        for model in (R1AReceipt, R1BReceipt, R1BDraft, RoundEnrollment):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
        for model in (Submission, ScoreEvent, TeamRoundState):
            assert await db.scalar(select(func.count()).select_from(model).where(
                model.round_code.in_(["R1A", "R1B"]))) == 0
            assert await db.scalar(select(func.count()).select_from(model).where(model.round_code == "R2")) == 1
        assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot).where(
            LeaderboardSnapshot.round_code.in_(["R1", "R1A", "R1B"]))) == 0
        assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot).where(
            LeaderboardSnapshot.round_code == "R2")) == 1
        assert not any((await db.scalars(select(PlayerDevice.active))).all())
        rounds = (await db.scalars(select(Round))).all()
        assert all(row.status == "DRAFT" and row.started_at is None and row.ended_at is None
                   and row.current_position is None and row.paused_at is None
                   and row.question_started_at is None and row.leaderboard_published_at is None
                   and row.rules_version is None and row.rules_snapshot is None for row in rounds)
        assert await db.scalar(select(func.count()).select_from(AdminAction).where(
            AdminAction.action == "HISTORICAL_SENTINEL")) == 1
        assert await db.scalar(select(func.count()).select_from(AdminAction).where(
            AdminAction.action == "RESET_ROUND_ONE")) == 1
    assert (await ctx["client"].get("/api/leaderboard/final")).json()["published"] is False


@pytest.mark.parametrize("status", ["DRAFT", "ENDED"])
async def test_reset_before_start_and_after_completion_is_repeatable(ctx, status):
    await authorize_rehearsal(ctx)
    await set_round_status(ctx, status, status)
    for _ in range(2):
        response = await ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY)
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("status", ["LIVE", "PAUSED"])
async def test_reset_rejects_running_or_paused_rehearsals_without_writes(ctx, status):
    from app.models import Round, ScoreEvent
    await authorize_rehearsal(ctx)
    await seed_combined(ctx)
    await set_round_status(ctx, "ENDED", status)
    response = await ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "ROUND_ACTIVE"
    async with ctx["local"]() as db:
        assert await db.scalar(select(Round.status).where(Round.code == "R1B")) == status
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 8


@pytest.mark.parametrize("bad", ["environment", "enabled", "token", "database_name", "marker_name", "marker_oid", "missing_marker"])
async def test_reset_database_authorization_fails_closed(ctx, bad):
    from app.api import admin
    from app.models import RehearsalAuthorization, Round
    await authorize_rehearsal(ctx)
    if bad in {"environment", "enabled", "token", "database_name"}:
        field, value = {
            "environment": ("environment", "production"), "enabled": ("r1_reset_enabled", False),
            "token": ("r1_rehearsal_token", "different-token"),
            "database_name": ("r1_reset_database_name", "competition_database"),
        }[bad]
        ctx["monkeypatch"].setattr(admin.settings, field, value)
    else:
        async with ctx["local"]() as db:
            marker = await db.get(RehearsalAuthorization, 1)
            if bad == "missing_marker":
                await db.delete(marker)
            elif bad == "marker_name":
                marker.database_name = "another_database"
            else:
                marker.database_oid += 1
            await db.commit()
    response = await ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "RESET_NOT_AUTHORIZED"
    async with ctx["local"]() as db:
        assert (await db.scalars(select(Round.status))).all() == ["DRAFT", "DRAFT"]


async def test_concurrent_resets_are_serialized_and_audited(ctx):
    from app.models import AdminAction, R1AReceipt, R1BReceipt, ScoreEvent
    await authorize_rehearsal(ctx)
    await seed_reset_progress(ctx)
    responses = await asyncio.wait_for(asyncio.gather(*(
        ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY)
        for _ in range(4)
    )), 5)
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    async with ctx["local"]() as db:
        assert await db.scalar(select(func.count()).select_from(AdminAction).where(
            AdminAction.action == "RESET_ROUND_ONE")) == 4
        assert await db.scalar(select(func.count()).select_from(ScoreEvent).where(
            ScoreEvent.round_code.in_(["R1A", "R1B"]))) == 0
        assert await db.scalar(select(func.count()).select_from(R1AReceipt)) == 0
        assert await db.scalar(select(func.count()).select_from(R1BReceipt)) == 0


async def test_failed_reset_rolls_back_publication_state_scores_and_sessions_then_recovers(ctx):
    from app.models import PlayerDevice, R1BReceipt, Round, ScoreEvent
    await authorize_rehearsal(ctx)
    await seed_reset_progress(ctx)
    original = (await ctx["client"].get("/api/leaderboard/final")).json()
    await install_failure(ctx, "RESET_ROUND_ONE")
    with pytest.raises(Exception, match="forced global transaction failure"):
        await ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY)
    async with ctx["local"]() as db:
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 8
        assert all((await db.scalars(select(PlayerDevice.active))).all())
        assert await db.scalar(select(func.count()).select_from(R1BReceipt)) == 1
        assert (await db.scalars(select(Round.status))).all() == ["ENDED", "ENDED"]
    assert (await ctx["client"].get("/api/leaderboard/final")).json() == original
    await remove_failure(ctx)
    response = await ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY)
    assert response.status_code == 200, response.text


async def test_cancelled_reset_releases_locks_and_can_retry(ctx):
    from app.models import Round
    await authorize_rehearsal(ctx)
    async with ctx["local"]() as blocker:
        await blocker.execute(select(Round).where(Round.code == "R1A").with_for_update())
        task = asyncio.create_task(ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY))
        done, _ = await asyncio.wait({task}, timeout=0.05)
        assert not done, "reset bypassed the existing round lock"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await blocker.rollback()
    response = await asyncio.wait_for(ctx["client"].post(
        "/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY), 5)
    assert response.status_code == 200, response.text


async def test_reset_interrupted_after_deletes_rolls_back_then_recovers(ctx):
    from app.api import admin
    from app.models import PlayerDevice, R1BReceipt, Round, ScoreEvent
    await authorize_rehearsal(ctx)
    await seed_reset_progress(ctx)
    before_commit = asyncio.Event()
    wait_forever = asyncio.Event()
    async with ctx["local"]() as db:
        async def interrupted_commit():
            await db.flush()
            before_commit.set()
            await wait_forever.wait()
        ctx["monkeypatch"].setattr(db, "commit", interrupted_commit)
        task = asyncio.create_task(admin.reset_round_one(
            admin.ResetRoundOneIn(**RESET_BODY), {"sub": "r1-global-admin"}, db))
        await asyncio.wait_for(before_commit.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await db.rollback()
    async with ctx["local"]() as db:
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 8
        assert await db.scalar(select(func.count()).select_from(R1BReceipt)) == 1
        assert all((await db.scalars(select(PlayerDevice.active))).all())
        assert (await db.scalars(select(Round.status))).all() == ["ENDED", "ENDED"]
    result = await ctx["client"].post("/api/admin/rounds/reset", headers=ctx["headers"], json=RESET_BODY)
    assert result.status_code == 200, result.text


async def test_reset_isolated_between_two_owned_disposable_databases(monkeypatch):
    """A real second DB verifies the guard is bound to PostgreSQL identity.

    Both UUID databases are created on the already guarded local test server;
    neither is a configured application/competition database.
    """
    require_disposable_test_database()
    from fastapi import HTTPException
    from sqlalchemy.engine import make_url
    from app.api import admin
    from app.db import Base
    from app.models import (Admin, LeaderboardSnapshot, RehearsalAuthorization,
                            Round, ScoreEvent, Team)

    url = make_url(os.environ["DATABASE_URL"])
    suffix = uuid4().hex
    names = ["round1_disposable_rehearsal_" + suffix,
             "round1_disposable_competition_" + suffix]
    root = create_async_engine(url, isolation_level="AUTOCOMMIT")
    created, engines, locals = [], [], []
    token = "two-database-disposable-test-token-at-least-32-characters"
    try:
        for name in names:
            async with root.connect() as connection:
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
            created.append(name)
            engine = create_async_engine(url.set(database=name))
            engines.append(engine)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            local = async_sessionmaker(engine, expire_on_commit=False)
            locals.append(local)
            async with local() as db:
                team = Team(code="SENTINEL", name="Preserved", login_username="sentinel",
                            password_hash="preserved-team-password")
                db.add_all([team, Admin(username="isolation-admin", password_hash="preserved-admin-password"),
                            Round(code="R1A", status="ENDED"), Round(code="R1B", status="ENDED")])
                await db.flush()
                db.add_all([ScoreEvent(team_id=team.id, round_code="R1A", points=789, reason="sentinel"),
                            LeaderboardSnapshot(round_code="R1A", data_json="[]")])
                await db.commit()
        async with locals[0]() as db:
            rehearsal_oid = await db.scalar(text(
                "SELECT oid FROM pg_database WHERE datname=current_database()"))
            db.add(RehearsalAuthorization(id=1, database_name=names[0], database_oid=rehearsal_oid,
                                         token_sha256=hashlib.sha256(token.encode()).hexdigest()))
            await db.commit()
        for key, value in {"environment": "rehearsal", "r1_reset_enabled": True,
                           "r1_reset_database_name": names[0], "r1_rehearsal_token": token}.items():
            monkeypatch.setattr(admin.settings, key, value)
        async with locals[0]() as db:
            result = await admin.reset_round_one(admin.ResetRoundOneIn(**RESET_BODY),
                                                 {"sub": "isolation-admin"}, db)
            assert result["ok"] is True
        async with locals[0]() as db:
            assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 0
            assert (await db.scalars(select(Round.status))).all() == ["DRAFT", "DRAFT"]
        # Pointing identical process settings at another database does not make
        # it rehearsal. Even changing the label and copying the guard fails.
        monkeypatch.setattr(admin.settings, "r1_reset_database_name", names[1])
        async with locals[1]() as db:
            with pytest.raises(HTTPException) as missing:
                await admin.reset_round_one(admin.ResetRoundOneIn(**RESET_BODY),
                                             {"sub": "isolation-admin"}, db)
            assert missing.value.detail["code"] == "RESET_NOT_AUTHORIZED"
            await db.rollback()
            db.add(RehearsalAuthorization(id=1, database_name=names[1], database_oid=rehearsal_oid,
                                         token_sha256=hashlib.sha256(token.encode()).hexdigest()))
            await db.commit()
        async with locals[1]() as db:
            with pytest.raises(HTTPException) as copied:
                await admin.reset_round_one(admin.ResetRoundOneIn(**RESET_BODY),
                                             {"sub": "isolation-admin"}, db)
            assert copied.value.detail["code"] == "RESET_NOT_AUTHORIZED"
            await db.rollback()
            assert await db.scalar(select(ScoreEvent.points)) == 789
            assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot)) == 1
            assert (await db.scalars(select(Round.status))).all() == ["ENDED", "ENDED"]
            assert await db.scalar(select(Team.password_hash)) == "preserved-team-password"
    finally:
        for engine in engines:
            await engine.dispose()
        for name in reversed(created):
            async with root.connect() as connection:
                await connection.execute(text(f'DROP DATABASE "{name}"'))
        await root.dispose()
