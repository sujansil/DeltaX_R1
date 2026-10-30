import json
import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.competition_rules import CompetitionRulesError, rules_from_dict
from app.scoring import r1a_points, r1a_result


def configured(*, count=5, turns=4):
    return rules_from_dict({
        "version": "test-v2",
        "r1a": {"question_count": count, "question_duration_ms": 20_000,
                "full_score_until_ms": 5_000, "decay_end_ms": 15_000,
                "maximum_correct_score": 800, "minimum_correct_score": 200,
                "wrong_answer_score": -300, "unanswered_score": 0},
        "r1b": {"turn_count": turns, "total_answering_budget_ms": 600_000,
                "correct_score": 5000, "wrong_score": -1200,
                "skip_unanswered_score": 0, "default_timer_mode": "PAUSE_DURING_HANDOVER"},
    })


def test_non_default_r1a_boundaries_and_derived_decay():
    rules = configured().r1a
    assert r1a_points(5_000, rules) == 800
    assert r1a_points(10_000, rules) == 500
    assert r1a_points(15_000, rules) == 200
    assert r1a_points(19_999, rules) == 200
    assert r1a_result(False, 1, rules) == -300
    assert r1a_result(None, 1, rules) == 0


def test_snapshot_round_trip_and_public_contract():
    rules = configured()
    assert rules_from_dict(json.loads(rules.snapshot_json())) == rules
    assert rules.public_dict()["r1b"]["total_answering_budget_ms"] == 600_000


def test_started_round_uses_snapshot_for_duration_and_final_position():
    from app.api.r1a import position_at
    rules = configured()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    round_obj = SimpleNamespace(status="LIVE", current_position=5,
                                question_started_at=start,
                                rules_snapshot=rules.snapshot_json())
    assert position_at(round_obj, start + timedelta(milliseconds=19_999)) == 5
    assert position_at(round_obj, start + timedelta(milliseconds=20_000)) is None


@pytest.mark.parametrize("change", [
    lambda d: d["r1a"].update(question_count=0),
    lambda d: d["r1a"].update(full_score_until_ms=15_000),
    lambda d: d["r1a"].update(minimum_correct_score=900),
    lambda d: d["r1b"].update(turn_count=5),
    lambda d: d["r1b"].update(default_timer_mode="OTHER"),
])
def test_invalid_rules_are_rejected(change):
    data = json.loads(configured().snapshot_json())
    change(data)
    with pytest.raises(CompetitionRulesError):
        rules_from_dict(data)
