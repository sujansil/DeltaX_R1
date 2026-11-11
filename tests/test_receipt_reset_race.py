"""Exercise reset at the durable receipt handoff and final response read.

The mocked database removes precisely the row reset deletes. Running the real
submit handlers verifies that stale requests return 409 and never re-score.
"""
from datetime import timedelta
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.competition_rules import configured_rules
from app.timing import utc_now


def database(receipt=None):
    db = MagicMock()
    db.connection = AsyncMock()
    db.commit = AsyncMock()
    db.scalar = AsyncMock(return_value=receipt)
    db.get = AsyncMock(return_value=None)
    db.add.side_effect = lambda row: setattr(row, "id", 17)
    return db


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("stage", ["receipt", "response"])
async def test_r1a_reset_during_two_phase_submission_rejects_safely(monkeypatch, stage):
    from app.api import r1a
    now = utc_now()
    round_obj = SimpleNamespace(code="R1A", status="LIVE", current_position=1,
        question_started_at=now-timedelta(seconds=1), rules_snapshot=None)
    receipt = SimpleNamespace(id=17, status="ACCEPTED", submission_id=44,
                              question_position=1)
    db = database(None if stage == "receipt" else receipt)
    monkeypatch.setattr(r1a, "lock_round", AsyncMock(return_value=round_obj))
    resolver = AsyncMock()
    monkeypatch.setattr(r1a, "resolve_receipt", resolver)
    with pytest.raises(HTTPException) as error:
        await r1a.submit(r1a.SubmitMCQ(option_id=3), {"team_id": 7}, db)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "SUBMISSION_STATE_CHANGED"
    resolver.assert_not_awaited()
    assert db.commit.await_count == (1 if stage == "receipt" else 2)
    assert db.add.call_count == 1  # admission only, no recreated receipt/outcome


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("stage", ["receipt", "response"])
async def test_r1b_reset_during_two_phase_submission_rejects_safely(monkeypatch, stage):
    from app.api import r1b
    from app import question_reveal
    now = utc_now()
    turn_id = "00000000-0000-0000-0000-000000000001"
    rules = configured_rules()
    rules = replace(rules, r1b=replace(rules.r1b, show_results_after_each_question=True))
    round_obj = SimpleNamespace(code="R1B", status="LIVE", rules_snapshot=rules.snapshot_json())
    state = SimpleNamespace(turn_status="ACTIVE", current_turn_id=turn_id,
        ready_at=now-timedelta(seconds=1), current_question_position=1,
        current_player=1, clock_running_since=now-timedelta(seconds=1),
        team_deadline=now+timedelta(minutes=5), remaining_time_ms=300000)
    receipt = SimpleNamespace(id=17, status="ACCEPTED", submission_id=44,
                              question_position=1)
    db = database(None if stage == "receipt" else receipt)
    monkeypatch.setattr(r1b, "require_live_round", AsyncMock(return_value=round_obj))
    monkeypatch.setattr(r1b, "locked_state", AsyncMock(return_value=state))
    resolver = AsyncMock()
    monkeypatch.setattr(r1b, "resolve_receipt", resolver)
    monkeypatch.setattr(question_reveal, "read_leaderboard", AsyncMock(return_value={}))
    with pytest.raises(HTTPException) as error:
        await r1b.submit(r1b.SubmitNumeric(turn_id=turn_id, answer="42"), {"team_id": 7}, db)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "SUBMISSION_STATE_CHANGED"
    assert resolver.await_count == (0 if stage == "receipt" else 1)
    assert db.commit.await_count == (1 if stage == "receipt" else 2)
    assert db.add.call_count == 1
