import asyncio
import json
import os
from datetime import timedelta
from dataclasses import replace
import pytest
from sqlalchemy import func, select
from tests.db_guard import require_disposable_test_database
pytestmark=[pytest.mark.database,pytest.mark.asyncio(loop_scope="session"),pytest.mark.skipif(os.getenv("RUN_DB_TESTS")!="1",reason="requires disposable PostgreSQL")]


@pytest.mark.parametrize("mode", ["MANUAL", "AUTO"])
@pytest.mark.parametrize("visible", [False, True])
async def test_r1a_intermediate_visibility_matrix(mode, visible):
    require_disposable_test_database()
    from tests.test_player_bootstrap import seed_r1a, get_bootstrap
    from app.competition_rules import configured_rules
    from app.db import SessionLocal
    from app.models import Round, ScoreEvent, Submission
    token = await seed_r1a("LIVE", age_seconds=31, accepted=True)
    async with SessionLocal() as db:
        round_obj = await db.scalar(select(Round).where(Round.code == "R1A"))
        rules = configured_rules()
        rules = replace(rules, r1a=replace(
            rules.r1a, advance_mode=mode,
            show_results_after_each_question=visible))
        round_obj.rules_snapshot = rules.snapshot_json()
        submission = await db.scalar(select(Submission).where(Submission.round_code == "R1A"))
        db.add(ScoreEvent(team_id=submission.team_id, round_code="R1A",
                          submission_id=submission.id, points=submission.points,
                          reason="MATRIX"))
        await db.commit()
    data = (await get_bootstrap(token)).json()
    assert data["rules"]["r1a"]["show_results_after_each_question"] is visible
    assert data["leaderboard"]["published"] is visible
    assert (data["own_outcome"]["result"] is not None) is visible
    assert (data["score_total"] is not None) is visible


async def test_accepted_points_hidden_until_close_then_automatic_board():
    require_disposable_test_database()
    from tests.test_player_bootstrap import seed_r1a,get_bootstrap
    from app.db import SessionLocal
    from app.models import Round,ScoreEvent,Submission,LeaderboardSnapshot
    from app.timing import utc_now
    token=await seed_r1a("LIVE",age_seconds=1,accepted=True)
    async with SessionLocal() as db:
        s=await db.scalar(select(Submission).where(Submission.round_code=="R1A"))
        db.add(ScoreEvent(team_id=s.team_id,round_code="R1A",submission_id=s.id,points=1000,reason="TEST"));await db.commit()
    early=(await get_bootstrap(token)).json()
    assert early["score_total"] is None and early["own_outcome"]["accepted"]
    assert early["own_outcome"]["points"] is None and early["own_outcome"]["result"] is None
    assert early["own_outcome"]["score_revealed"] is False and not early["leaderboard"]["published"]
    async with SessionLocal() as db:
        r=await db.scalar(select(Round).where(Round.code=="R1A"));r.question_started_at=utc_now()-timedelta(seconds=31);await db.commit()
    closed=(await get_bootstrap(token)).json()
    assert closed["score_total"]==1000 and closed["own_outcome"]["points"]==1000
    assert closed["leaderboard"]["after_question"]==1 and closed["leaderboard"]["mine"]["score"]==1000
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot))==1


async def test_concurrent_reconcilers_make_one_snapshot_without_advancing():
    require_disposable_test_database()
    from tests.test_r1a_reconciliation import reset_data,seed
    from app.db import SessionLocal
    from app.models import Round,LeaderboardSnapshot
    from app.r1a_reconciliation import reconcile_r1a
    await reset_data();await seed()
    async def worker():
        async with SessionLocal() as db:await reconcile_r1a(db);await db.commit()
    await asyncio.wait_for(asyncio.gather(worker(),worker(),worker()),5)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(LeaderboardSnapshot))==1
        r=await db.scalar(select(Round).where(Round.code=="R1A"));assert r.current_position==1 and r.status=="LIVE"


async def test_paused_scores_and_premature_legacy_publication_stay_hidden():
    require_disposable_test_database()
    from tests.test_player_bootstrap import seed_r1a,get_bootstrap
    from app.db import SessionLocal
    from app.models import LeaderboardSnapshot
    token=await seed_r1a("PAUSED",age_seconds=1,accepted=True)
    async with SessionLocal() as db:
        db.add(LeaderboardSnapshot(round_code="R1A",data_json=json.dumps([{"rank":1,"team_id":1,"score":9999,"team_name":"Premature"}])));await db.commit()
    data=(await get_bootstrap(token)).json()
    assert data["own_outcome"]["points"] is None and data["leaderboard"]["published"] is False
