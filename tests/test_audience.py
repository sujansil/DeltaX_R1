from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import pytest

from app.audience import Controls, select_slide, round_key, SettingsChange
from app.competition_rules import configured_rules

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def sample(code="R1A", status="LIVE", *, age=10, mode="AUTO", delay=8000):
    rules = configured_rules().public_dict()
    rules["r1a"].update(advance_mode=mode, auto_advance_delay_ms=delay)
    return SimpleNamespace(id=1, code=code, status=status, current_position=1,
                           question_started_at=NOW-timedelta(seconds=age), paused_at=None,
                           started_at=NOW-timedelta(minutes=10), ended_at=None,
                           r1a_result_due_at=None, rules_snapshot=json.dumps(rules))


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("board", [False, True])
@pytest.mark.parametrize("mode", ["AUTO", "MANUAL"])
def test_live_always_wins_and_answer_is_mandatory(preview, board, mode):
    r = sample(mode=mode)
    settings = Controls(preview_upcoming=preview, leaderboard_visible=board)
    assert select_slide(r, settings, NOW)["view"] == "QUESTION"
    r.question_started_at = NOW-timedelta(seconds=30)
    if mode == "AUTO":
        r.r1a_result_due_at = NOW+timedelta(seconds=8)
    assert select_slide(r, settings, NOW)["view"] == "ANSWER"
    later = select_slide(r, settings, NOW+timedelta(seconds=4))
    assert later["view"] == ("ANSWER_LEADERBOARD" if board else "PREVIEW" if preview else "ANSWER")
    latest = select_slide(r, settings, NOW+timedelta(seconds=7))
    assert latest["view"] == ("PREVIEW" if preview else "ANSWER_LEADERBOARD" if board else "ANSWER")
    r.current_position=2;r.question_started_at=NOW+timedelta(seconds=8);r.r1a_result_due_at=None
    assert select_slide(r, settings, NOW+timedelta(seconds=8))["view"] == "QUESTION"


@pytest.mark.parametrize("delay", [0, 1, 300, 1000, 3000, 8000])
def test_auto_short_intervals_do_not_mutate_or_extend_clock(delay):
    r=sample(age=30,delay=delay);r.r1a_result_due_at=NOW+timedelta(milliseconds=delay)
    before=vars(r).copy()
    assert select_slide(r,Controls(preview_upcoming=True,leaderboard_visible=True),NOW)["view"] in {"ANSWER","PREVIEW","ANSWER_LEADERBOARD"}
    assert vars(r)==before


def test_draft_preview_off_and_on_never_contains_an_answer_stage():
    r=sample(status="DRAFT")
    assert select_slide(r,Controls(),NOW)["position"] is None
    assert select_slide(r,Controls(preview_upcoming=True),NOW)=={"view":"PREVIEW","position":1}


def test_pause_keeps_live_question_until_effective_deadline():
    r=sample(status="PAUSED");r.paused_at=NOW
    assert select_slide(r,Controls(preview_upcoming=True),NOW+timedelta(hours=2))["view"]=="QUESTION"


def test_final_and_early_end_do_not_preview_another_question():
    r=sample(status="ENDED");r.ended_at=NOW
    assert select_slide(r,Controls(preview_upcoming=True),NOW)["view"]=="ANSWER"
    assert select_slide(r,Controls(preview_upcoming=True),NOW+timedelta(seconds=4))["view"]=="ANSWER"


@pytest.mark.parametrize("status", ["DRAFT","LIVE","PAUSED"])
def test_relay_active_always_standby_regardless_of_review_and_switches(status):
    r=sample("R1B",status)
    for step in [-1,0,1,8]:
        c=Controls(preview_upcoming=True,leaderboard_visible=True,review_step=step,review_round_key=round_key(r))
        assert select_slide(r,c,NOW)=={"view":"RELAY_STANDBY","position":None}


def test_relay_ended_requires_explicit_persisted_step_and_matching_round():
    r=sample("R1B","ENDED")
    assert select_slide(r,Controls(),NOW)["view"]=="REVIEW_READY"
    for step in range(8):
        c=Controls(review_step=step,review_round_key=round_key(r))
        assert select_slide(r,c,NOW)=={"view":"REVIEW_ANSWER" if step%2 else "REVIEW_QUESTION","position":step//2+1}
    c=Controls(review_step=8,review_round_key=round_key(r))
    assert select_slide(r,c,NOW)["view"]=="THANK_YOU"
    c.leaderboard_visible=True
    assert select_slide(r,c,NOW)["view"]=="LEADERBOARD"
    r.started_at=NOW
    assert select_slide(r,c,NOW)["view"]=="REVIEW_READY"


def test_controls_strict_schema_rejects_extra_private_and_ambiguous_values():
    from pydantic import ValidationError
    for data in [{"preview_upcoming":"false"},{"version":2},{"password":"secret"},{"review_step":-2}]:
        with pytest.raises(ValidationError):Controls.model_validate(data)
    with pytest.raises(ValidationError):SettingsChange(expected_revision=True,preview_upcoming=False,leaderboard_visible=False)
