from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.combined_leaderboard import rank_rows, ready


def row(team_id, a, b):
    return {"team_id": team_id, "team_code": f"T{team_id:02}",
            "team_name": f"Team {team_id}", "r1a_score": a,
            "r1b_score": b, "score": a + b}


def test_combined_order_and_signed_totals():
    rows, ties, qualification = rank_rows([
        row(4, 0, -1500), row(1, 1000, 4000), row(3, 0, 0), row(2, -500, 4000)])
    assert [(r["team_id"], r["score"]) for r in rows] == [(1, 5000), (2, 3500), (3, 0), (4, -1500)]
    assert ties == []
    assert qualification["team_ids"] == [1, 2, 3, 4]


def test_equal_scores_share_rank_and_display_order_is_stable():
    inputs = [row(5, -500, 0), row(3, 1000, 0), row(1, 0, 1000), row(2, -500, 0)]
    first = rank_rows(deepcopy(inputs))
    second = rank_rows(list(reversed(deepcopy(inputs))))
    assert first == second
    assert [(r["team_id"], r["rank"]) for r in first[0]] == [(1, 1), (3, 1), (2, 3), (5, 3)]
    assert first[1] == [{"rank": 1, "score": 1000, "team_ids": [1, 3]},
                        {"rank": 3, "score": -500, "team_ids": [2, 5]}]


def test_cutoff_tie_does_not_use_team_code_as_qualifying_rule():
    rows, _, qualification = rank_rows([row(i, 0, 0) for i in range(18, 0, -1)])
    assert len(rows) == 18
    assert qualification["status"] == "BLOCKED_CUTOFF_TIE"
    assert qualification["team_ids"] == []
    assert qualification["cutoff_tie_team_ids"] == list(range(1, 19))


def test_ties_entirely_above_cutoff_allow_frozen_roster():
    _, _, qualification = rank_rows([row(i, 1 if i <= 16 else 0, 0) for i in range(1, 19)])
    assert qualification["status"] == "READY"
    assert qualification["team_ids"] == list(range(1, 17))


@pytest.mark.parametrize("a,b", [("DRAFT", "DRAFT"), ("ENDED", "LIVE"),
                                ("PAUSED", "ENDED"), ("ENDED", "DRAFT")])
def test_both_subrounds_must_officially_end(a, b):
    assert not ready({"R1A": SimpleNamespace(status=a), "R1B": SimpleNamespace(status=b)})


def test_both_ended_and_missing_round():
    ended = SimpleNamespace(status="ENDED")
    assert ready({"R1A": ended, "R1B": ended})
    assert not ready({"R1A": ended})


@pytest.mark.asyncio(loop_scope="session")
async def test_generic_publisher_preserves_its_previous_round_code_contract(monkeypatch):
    import json
    from unittest.mock import AsyncMock, MagicMock
    from app.services import LeaderboardService
    service = LeaderboardService()
    rows = [{"team_id": 7, "score": -1500}]
    calculate = AsyncMock(return_value=rows)
    monkeypatch.setattr(service, "calculate", calculate)
    db = MagicMock()
    db.flush = AsyncMock()
    snapshot, result = await service.publish(db, "LEGACY", kind="CUSTOM", metadata={"source": "caller"})
    calculate.assert_awaited_once_with(db, "LEGACY")
    assert snapshot.round_code == "LEGACY" and result == rows
    assert json.loads(snapshot.data_json) == {"kind": "CUSTOM", "rows": rows, "source": "caller"}
