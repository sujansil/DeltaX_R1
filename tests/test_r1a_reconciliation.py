import asyncio
import os
from datetime import timedelta

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
    from app.models import (AdminAction, LeaderboardSnapshot, Question, QuestionOption,
                            R1AReceipt, Round, RoundEnrollment, ScoreEvent, Submission, Team)
    async with SessionLocal() as db:
        for model in (AdminAction, LeaderboardSnapshot, ScoreEvent, R1AReceipt, Submission,
                      RoundEnrollment, QuestionOption, Question, Round, Team):
            await db.execute(delete(model))
        await db.commit()


async def seed(*, position=1, age_seconds=31, paused=False):
    from dataclasses import replace
    from app.competition_rules import configured_rules
    from app.db import SessionLocal
    from app.models import Question, QuestionOption, Round, RoundEnrollment, Submission, Team
    from app.timing import utc_now
    async with SessionLocal() as db:
        connected = Team(code="CONNECTED", name="Connected", login_username="connected")
        disconnected = Team(code="DISCONNECTED", name="Disconnected", login_username="disconnected")
        ineligible = Team(code="LATE", name="Late", login_username="late")
        db.add_all((connected, disconnected, ineligible))
        await db.flush()
        q = Question(round_code="R1A", position=position, answer_type="MCQ")
        db.add(q)
        await db.flush()
        option = QuestionOption(question_id=q.id, position=1, option_text="correct")
        db.add(option)
        await db.flush()
        q.correct_option_id = option.id
        started = utc_now() - timedelta(seconds=age_seconds)
        rules = configured_rules()
        rules = replace(rules, r1a=replace(rules.r1a, advance_mode="MANUAL"))
        db.add(Round(code="R1A", status="PAUSED" if paused else "LIVE",
                     started_at=started, current_position=position,
                     question_started_at=started,
                     rules_version=rules.version, rules_snapshot=rules.snapshot_json(),
                     paused_at=utc_now() if paused else None))
        db.add_all((
            RoundEnrollment(team_id=connected.id, round_code="R1A", enrolled_at=started),
            RoundEnrollment(team_id=disconnected.id, round_code="R1A", enrolled_at=started),
        ))
        accepted_at = started + timedelta(seconds=5)
        db.add(Submission(team_id=connected.id, round_code="R1A",
                          question_position=position, selected_option_id=option.id,
                          submitted_at=accepted_at, recorded_at=accepted_at,
                          result="CORRECT", points=1234))
        await db.commit()
        return connected.id, disconnected.id, ineligible.id, option.id, started


async def test_preserves_answer_and_reconciles_disconnected_roster_idempotently():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import ScoreEvent, Submission
    from app.r1a_reconciliation import reconcile_r1a
    await reset_data()
    connected, disconnected, ineligible, _, started = await seed()
    async with SessionLocal() as db:
        result = await reconcile_r1a(db)
        await db.commit()
        assert result.inserted == 1
    async with SessionLocal() as db:
        await reconcile_r1a(db)
        await db.commit()
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Submission).order_by(Submission.team_id))).all()
        assert [(r.team_id, r.result, r.points) for r in rows] == [
            (connected, "CORRECT", 1234), (disconnected, "UNANSWERED", 0)]
        unanswered = rows[1]
        assert unanswered.submitted_at is None
        assert unanswered.timed_out_at == started + timedelta(seconds=30)
        assert unanswered.recorded_at >= unanswered.timed_out_at
        assert all(r.team_id != ineligible for r in rows)
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 1


async def test_pending_pre_cutoff_receipt_is_scored_by_receipt_time_before_publish():
    require_disposable_test_database()
    import json
    from app.db import SessionLocal
    from app.models import LeaderboardSnapshot, R1AReceipt, Round, Submission
    from app.question_reveal import snapshot_payload
    from app.r1a_reconciliation import reconcile_r1a
    await reset_data()
    _, team_id, _, option_id, started = await seed(age_seconds=31)
    decay_rules = {
        "version": "receipt-decay-test", "r1a": {
            "advance_mode": "MANUAL", "auto_advance_delay_ms": 2000,
            "decay_enabled": True, "question_count": 23,
            "question_duration_ms": 30000, "full_score_until_ms": 10000,
            "decay_end_ms": 25000, "maximum_correct_score": 1000,
            "minimum_correct_score": 250, "wrong_answer_score": -500,
            "unanswered_score": 0}, "r1b": {
            "turn_count": 4, "total_answering_budget_ms": 1680000,
            "correct_score": 4000, "wrong_score": -1500,
            "skip_unanswered_score": 0, "default_timer_mode": "CONTINUOUS"}}
    async with SessionLocal() as db:
        round_obj = await db.scalar(select(Round).where(Round.code == "R1A"))
        round_obj.rules_version = decay_rules["version"]
        round_obj.rules_snapshot = json.dumps(decay_rules)
        db.add(R1AReceipt(team_id=team_id, session_id="", option_id=option_id,
            received_at=started + timedelta(milliseconds=15000), question_position=1,
            question_started_at=started, question_deadline=started + timedelta(seconds=30)))
        await db.commit()
    async with SessionLocal() as db:
        await reconcile_r1a(db, now=started + timedelta(seconds=31))
        await db.commit()
    async with SessionLocal() as db:
        submission = await db.scalar(select(Submission).where(Submission.team_id == team_id))
        receipt = await db.scalar(select(R1AReceipt).where(R1AReceipt.team_id == team_id))
        snapshot = await db.scalar(select(LeaderboardSnapshot).order_by(LeaderboardSnapshot.id.desc()))
        assert (submission.submitted_at, submission.elapsed_ms, submission.points) == (
            started + timedelta(milliseconds=15000), 15000, 750)
        assert receipt.status == "ACCEPTED" and receipt.submission_id == submission.id
        row = next(row for row in snapshot_payload(snapshot.data_json)["rows"] if row["team_id"] == team_id)
        assert row["score"] == 750


async def test_one_hundred_distinct_teams_submit_concurrently_with_decay():
    require_disposable_test_database()
    from dataclasses import replace
    from app.api import r1a
    from app.competition_rules import configured_rules
    from app.db import SessionLocal
    from app.models import Question, QuestionOption, Round, ScoreEvent, Submission, Team
    from app.scoring import r1a_points
    from app.timing import utc_now
    await reset_data()
    rules = configured_rules()
    rules = replace(rules, r1a=replace(rules.r1a, decay_enabled=True,
        full_score_until_ms=10_000, decay_end_ms=25_000, advance_mode="MANUAL"))
    started = utc_now() - timedelta(milliseconds=15_000)
    async with SessionLocal() as db:
        question = Question(round_code="R1A", position=1, answer_type="MCQ")
        db.add(question); await db.flush()
        option = QuestionOption(question_id=question.id, position=1, option_text="correct")
        db.add(option); await db.flush(); question.correct_option_id = option.id
        teams = [Team(code=f"LOAD-{i:03}", name=f"Load {i}", login_username=f"load-{i:03}")
                 for i in range(100)]
        db.add_all(teams); await db.flush()
        team_ids = [team.id for team in teams]
        option_id = option.id
        db.add(Round(code="R1A", status="LIVE", started_at=started,
            current_position=1, question_started_at=started,
            rules_version=rules.version, rules_snapshot=rules.snapshot_json()))
        await db.commit()

    async def submit_one(team_id):
        async with SessionLocal() as db:
            return await r1a.submit(r1a.SubmitMCQ(option_id=option_id), {"team_id": team_id}, db)
    results = await asyncio.gather(*(submit_one(team_id) for team_id in team_ids))
    assert len(results) == 100 and all(result["accepted"] for result in results)
    async with SessionLocal() as db:
        submissions = (await db.scalars(select(Submission).order_by(Submission.team_id))).all()
        assert len(submissions) == 100
        assert await db.scalar(select(func.count()).select_from(ScoreEvent)) == 100
        assert all(row.points == r1a_points(row.elapsed_ms, rules.r1a) for row in submissions)


async def test_round_start_snapshots_enabled_teams_once():
    require_disposable_test_database()
    from app.api import admin
    from app.db import SessionLocal
    from app.models import Question, QuestionOption, Round, RoundEnrollment, Team
    await reset_data()
    async with SessionLocal() as db:
        eligible = Team(code="ELIGIBLE", name="Eligible", login_username="eligible")
        disabled = Team(code="DISABLED", name="Disabled", login_username="disabled", enabled=False)
        db.add_all((eligible, disabled, Round(code="R1A")))
        for position in range(1, 24):
            q = Question(round_code="R1A", position=position, answer_type="MCQ")
            db.add(q); await db.flush()
            option = QuestionOption(question_id=q.id, position=1, option_text="correct")
            db.add(option); await db.flush(); q.correct_option_id = option.id
        await db.commit()
        await admin.start_round(admin.RoundAction(round_code="R1A"), {"role": "admin"}, db)
        disabled.enabled = True
        await db.commit()
    async with SessionLocal() as db:
        roster = (await db.scalars(select(RoundEnrollment.team_id))).all()
        assert roster == [eligible.id]


async def test_concurrent_reconciliation_and_no_future_outcomes():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import Submission
    from app.r1a_reconciliation import reconcile_r1a
    await reset_data()
    await seed()
    async with SessionLocal() as one, SessionLocal() as two:
        async def run(db):
            result = await reconcile_r1a(db)
            await db.commit()
            return result
        results = await asyncio.gather(run(one), run(two))
        assert sum(result.inserted for result in results) == 1
    async with SessionLocal() as db:
        positions = (await db.scalars(select(Submission.question_position))).all()
        assert positions == [1, 1]


async def test_pause_does_not_expire_and_q23_background_pass_ends_at_deadline():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import Round, Submission
    from app.r1a_reconciliation import reconcile_r1a
    await reset_data()
    _, _, _, _, started = await seed(position=1, paused=True)
    async with SessionLocal() as db:
        result = await reconcile_r1a(db, now=started + timedelta(minutes=5))
        await db.commit()
        assert result.inserted == 0
        assert await db.scalar(select(func.count()).select_from(Submission)) == 1

    await reset_data()
    await seed(position=23)
    async with SessionLocal() as db:
        result = await reconcile_r1a(db)
        await db.commit()
        round_obj = await db.scalar(select(Round).where(Round.code == "R1A"))
        assert result.round_ended
        assert round_obj.status == "ENDED"
        assert round_obj.ended_at == result.deadline


async def test_submit_boundary_and_reveal_reconcile_previous_only(monkeypatch):
    require_disposable_test_database()
    from app.api import admin, r1a
    from app.db import SessionLocal
    from app.models import Question, QuestionOption, Round, Submission
    await reset_data()
    _, _, submitting_team, option, started = await seed(age_seconds=29)
    monkeypatch.setattr(r1a, "utc_now", lambda: started + timedelta(seconds=29, milliseconds=999))
    async with SessionLocal() as db:
        result = await r1a.submit(r1a.SubmitMCQ(option_id=option), {"team_id": submitting_team}, db)
        assert result["position"] == 1

    await reset_data()
    _, disconnected, _, _, started = await seed(age_seconds=31)
    async with SessionLocal() as db:
        q2 = Question(round_code="R1A", position=2, answer_type="MCQ")
        db.add(q2); await db.flush()
        opt2 = QuestionOption(question_id=q2.id, position=1, option_text="correct")
        db.add(opt2); await db.flush(); q2.correct_option_id = opt2.id
        await db.commit()
    monkeypatch.setattr(admin, "utc_now", lambda: started + timedelta(seconds=31))
    async with SessionLocal() as db:
        out = await admin.next_question(admin.NextQuestion(expected_position=1), {"role": "admin"}, db)
        assert out["position"] == 2
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Submission).where(
            Submission.team_id == disconnected, Submission.question_position == 1)) == 1
        assert await db.scalar(select(func.count()).select_from(Submission).where(
            Submission.question_position == 2)) == 0


async def test_restart_recovery_uses_persisted_deadline():
    require_disposable_test_database()
    from app.db import SessionLocal
    from app.models import Submission
    from app.r1a_reconciliation import reconcile_r1a
    await reset_data()
    await seed(age_seconds=300)
    # A fresh session represents a newly started worker after a missed interval.
    async with SessionLocal() as fresh_worker:
        await reconcile_r1a(fresh_worker)
        await fresh_worker.commit()
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Submission).where(
            Submission.result == "UNANSWERED")) == 1


async def test_periodic_worker_finalizes_q23_without_request(monkeypatch):
    require_disposable_test_database()
    from app import main
    from app.db import SessionLocal
    from app.models import Round
    await reset_data()
    await seed(position=23)
    monkeypatch.setattr(main.settings, "r1a_reconciliation_interval_seconds", 0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(main._r1a_reconciliation_loop(stop))
    try:
        for _ in range(50):
            async with SessionLocal() as db:
                status = await db.scalar(select(Round.status).where(Round.code == "R1A"))
            if status == "ENDED":
                break
            await asyncio.sleep(0.01)
        assert status == "ENDED"
    finally:
        stop.set()
        await task
