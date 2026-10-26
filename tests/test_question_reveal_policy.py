import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest


def sample(status="LIVE", age=10):
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    return SimpleNamespace(status=status, current_position=3,
        question_started_at=now-timedelta(seconds=age), paused_at=None,
        ended_at=None, leaderboard_published_at=None), now


def test_question_score_changes_only_at_exact_deadline():
    from app.question_reveal import closed_position
    r, now = sample(); deadline = r.question_started_at+timedelta(seconds=30)
    assert closed_position(r, deadline-timedelta(milliseconds=1)) == 2
    assert closed_position(r, deadline) == 3
    assert closed_position(r, deadline+timedelta(milliseconds=1)) == 3


def test_global_pause_does_not_reveal_while_wall_clock_runs():
    from app.question_reveal import closed_position, natural_close
    r, now = sample("PAUSED"); r.paused_at=now
    assert closed_position(r, now+timedelta(hours=1)) == 2
    assert natural_close(r, now+timedelta(hours=1)) is None


def test_early_end_preserves_score_without_claiming_natural_timeout():
    from app.question_reveal import closed_position, natural_close
    r, now = sample("ENDED"); r.ended_at=now
    assert closed_position(r, now) == 3
    assert natural_close(r, now+timedelta(hours=1)) is None


def test_draft_and_late_pause_visibility():
    from app.question_reveal import closed_position, natural_close
    r, now = sample("DRAFT", age=40)
    assert closed_position(r, now) == 0 and natural_close(r,now) is None
    r.status="PAUSED"; r.paused_at=now
    assert closed_position(r,now) == 3 and natural_close(r,now) is not None


def test_legacy_and_automatic_snapshots_decode():
    from app.question_reveal import KIND, snapshot_payload
    rows=[{"rank":1,"team_id":7,"score":1000}]
    assert snapshot_payload(json.dumps(rows))["rows"] == rows
    assert snapshot_payload(json.dumps({"kind":KIND,"after_question":3,"rows":rows}))["after_question"] == 3
    with pytest.raises(ValueError): snapshot_payload('{"unknown":true}')


@pytest.mark.asyncio(loop_scope="session")
async def test_existing_snapshot_is_idempotent():
    from app.question_reveal import ensure_question_snapshot
    r,now=sample(age=31);existing=SimpleNamespace(id=10)
    db=MagicMock();db.scalar=AsyncMock(return_value=existing)
    assert await ensure_question_snapshot(db,r,now) is existing
    db.add.assert_not_called()


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_not_created_before_deadline():
    from app.question_reveal import ensure_question_snapshot
    r,now=sample();db=MagicMock();db.scalar=AsyncMock()
    assert await ensure_question_snapshot(db,r,now) is None
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="session")
async def test_new_snapshot_metadata_and_timezone():
    from app.question_reveal import ensure_question_snapshot,snapshot_payload,KIND
    r,now=sample(age=31);db=MagicMock();db.scalar=AsyncMock(return_value=None);db.flush=AsyncMock()
    result=MagicMock();result.all.return_value=[SimpleNamespace(id=7,code="TEAM7",name="Team Seven",score=750)];db.execute=AsyncMock(return_value=result)
    snapshot=await ensure_question_snapshot(db,r,now);p=snapshot_payload(snapshot.data_json)
    assert p["kind"]==KIND and p["after_question"]==3
    assert p["question_deadline"].endswith("+00:00") and p["rows"][0]["score"]==750
    assert r.leaderboard_published_at==now


@pytest.mark.asyncio(loop_scope="session")
async def test_own_rank_outside_top16_is_preserved():
    from app.question_reveal import KIND,read_leaderboard
    rows=[{"rank":n,"team_id":n,"team_name":str(n),"score":100-n} for n in range(1,20)]
    snap=SimpleNamespace(id=3,published_at=datetime.now(timezone.utc),data_json=json.dumps({"kind":KIND,"after_question":1,"rows":rows}))
    db=MagicMock();db.scalar=AsyncMock(return_value=snap);b=await read_leaderboard(db,"R1A",19)
    assert len(b["data"])==16 and b["mine"]["rank"]==19 and b["automatic"] is True
