"""Isolated, read-only projection of gameplay into an audience presentation.

Only authenticated control writes append versioned records to the existing audit
table. No reconciliation, scoring, publication, session or gameplay writes here.
"""
from datetime import datetime, timedelta, timezone
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select, text

from .competition_rules import round_rules
from .models import Admin, AdminAction, Question, QuestionOption, Round
from .question_reveal import read_leaderboard
from .timing import utc_now

ACTION = "AUDIENCE_STATE_V1"
LOCK_KEY = 1748172061  # dedicated transaction lock; never takes a gameplay lock


class Controls(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    preview_upcoming: StrictBool = False
    leaderboard_visible: StrictBool = False
    review_step: int = Field(default=-1, ge=-1, le=200)
    review_round_key: str | None = Field(default=None, max_length=160)


class SettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    preview_upcoming: StrictBool
    leaderboard_visible: StrictBool


class ReviewChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    action: Literal["start", "next"]


def fail(code, message, status=409):
    raise HTTPException(status, {"code": code, "message": message})


async def controls(db):
    row = await db.scalar(select(AdminAction).where(AdminAction.action == ACTION)
                          .order_by(AdminAction.id.desc()).limit(1))
    if row is None:
        return Controls(), 0
    try:
        return Controls.model_validate_json(row.details), row.id
    except (ValueError, TypeError):
        fail("AUDIENCE_STATE_INVALID", "Audience settings require administrator recovery", 503)


def round_key(r):
    return f"{r.id}:{r.started_at.isoformat() if r.started_at else 'not-started'}"


async def write_controls(db, user, change):
    # READ COMMITTED + transaction lock: after the previous writer commits, the
    # revision read below sees it. Concurrent stale requests receive 409.
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
    current, revision = await controls(db)
    if change.expected_revision != revision:
        fail("AUDIENCE_REVISION_CONFLICT", "Audience controls changed; refresh before retrying")
    updated = current.model_copy(deep=True)
    if isinstance(change, SettingsChange):
        updated.preview_upcoming = change.preview_upcoming
        updated.leaderboard_visible = change.leaderboard_visible
    else:
        r = await db.scalar(select(Round).where(Round.code == "R1B"))
        if not r or r.status != "ENDED":
            fail("AUDIENCE_REVIEW_NOT_ENDED", "R1B review is available only after the entire round ends")
        count = round_rules(r).r1b.turn_count
        same = updated.review_round_key == round_key(r)
        step = updated.review_step if same else -1
        if change.action == "start":
            if step >= 0:
                fail("AUDIENCE_REVIEW_STARTED", "Review already started; use Next slide")
            step = 0
        else:
            if step < 0 or step >= count * 2:
                fail("AUDIENCE_REVIEW_BOUNDARY", "There is no next review slide")
            step += 1
        updated.review_step, updated.review_round_key = step, round_key(r)
    admin_id = await db.scalar(select(Admin.id).where(Admin.username == user.get("sub")))
    record = AdminAction(admin_id=admin_id, action=ACTION,
                         details=updated.model_dump_json())
    db.add(record)
    await db.flush()
    await db.commit()
    return {"revision": record.id, "preview_upcoming": updated.preview_upcoming,
            "leaderboard_visible": updated.leaderboard_visible}


def select_slide(r, settings, now):
    """Pure stage selection. Browser clocks never choose presentation stages."""
    if r is None:
        return {"view": "STANDBY", "position": None}
    if r.code == "R1B":
        if r.status != "ENDED":
            return {"view": "RELAY_STANDBY", "position": None}
        step = settings.review_step if settings.review_round_key == round_key(r) else -1
        count = round_rules(r).r1b.turn_count
        if step < 0:
            return {"view": "REVIEW_READY", "position": None}
        if step >= count * 2:
            return {"view": "LEADERBOARD" if settings.leaderboard_visible else "THANK_YOU", "position": None}
        return {"view": "REVIEW_ANSWER" if step % 2 else "REVIEW_QUESTION", "position": step // 2 + 1}
    rules = round_rules(r).r1a
    p = r.current_position
    if r.status == "DRAFT" or not p or not r.question_started_at:
        return {"view": "PREVIEW" if settings.preview_upcoming and r.status == "DRAFT" else "STANDBY",
                "position": 1 if settings.preview_upcoming and r.status == "DRAFT" else None}
    deadline = r.question_started_at + timedelta(milliseconds=rules.question_duration_ms)
    reference = r.paused_at if r.status == "PAUSED" and r.paused_at else now
    if r.status in ("LIVE", "PAUSED") and reference < deadline:
        return {"view": "QUESTION", "position": p, "deadline": deadline}
    # The answer is independent of leaderboard settings and publication state.
    # AUTO is anchored to the persisted result interval, which resume shifts.
    due = r.r1a_result_due_at
    result_start = (due - timedelta(milliseconds=rules.auto_advance_delay_ms) if due else
                    min(deadline, r.ended_at) if r.status == "ENDED" and r.ended_at else deadline)
    elapsed = max(0, (reference - result_start).total_seconds() * 1000)
    finite = rules.advance_mode == "AUTO" and due is not None
    window = rules.auto_advance_delay_ms if finite else None
    later = settings.leaderboard_visible or settings.preview_upcoming
    answer_ms = min(3000, window / 2) if finite and later else 3000
    has_next = p < rules.question_count and r.status != "ENDED"
    if elapsed < answer_ms:
        return {"view": "ANSWER", "position": p}
    if settings.preview_upcoming and has_next:
        # When standings are requested, give them a bounded stage before the
        # full-screen upcoming preview. OFF never suppresses the answer stage.
        board_ms = min(3000, max(0, window - answer_ms) / 2) if finite else 3000
        if not settings.leaderboard_visible or elapsed >= answer_ms + board_ms:
            return {"view": "PREVIEW", "position": p + 1}
    return {"view": "ANSWER_LEADERBOARD" if settings.leaderboard_visible else "ANSWER", "position": p}


async def question_data(db, code, position, reveal):
    q = await db.scalar(select(Question).where(Question.round_code == code,
                        Question.position == position, Question.enabled.is_(True)))
    if not q:
        return None, None
    result = {"id": q.id, "position": q.position, "type": q.answer_type,
              "text": q.question_text, "image_url": q.question_image_url, "options": []}
    answer = None
    if code == "R1A":
        options = (await db.scalars(select(QuestionOption).where(QuestionOption.question_id == q.id)
                                    .order_by(QuestionOption.position))).all()
        result["options"] = [{"id": o.id, "position": o.position, "text": o.option_text,
                              "image_url": o.image_url} for o in options]
        if reveal and any(o.id == q.correct_option_id for o in options):
            answer = {"question_id": q.id, "position": q.position, "type": "MCQ",
                      "correct_option_id": q.correct_option_id}
    elif reveal and q.correct_numeric is not None:
        answer = {"question_id": q.id, "position": q.position, "type": "NUMERIC",
                  "value": format(q.correct_numeric, "f")}
    return result, answer


async def snapshot(db, *, admin=False, now=None):
    # Must be the first SQL in this request. A single MVCC snapshot prevents
    # mixing a new live question with an old answer or control revision.
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    settings, revision = await controls(db)
    rounds = (await db.scalars(select(Round))).all()
    a = next((r for r in rounds if r.code == "R1A"), None)
    b = next((r for r in rounds if r.code == "R1B"), None)
    r = (a if a and a.status in ("LIVE", "PAUSED") else
         b if b and b.status in ("LIVE", "PAUSED", "ENDED") else a)
    now = now or utc_now()
    slide = select_slide(r, settings, now)
    view, p = slide["view"], slide["position"]
    question, answer = (await question_data(db, r.code, p, view in ("ANSWER", "ANSWER_LEADERBOARD", "REVIEW_ANSWER"))
                        if r and p else (None, None))
    if p and question is None:
        # Missing media/content/configuration is not permission to skip slides.
        view = "CONTENT_UNAVAILABLE"
    board = None
    if r and settings.leaderboard_visible and view in ("ANSWER_LEADERBOARD", "LEADERBOARD"):
        board = await read_leaderboard(db, r.code, include_intermediate=r.code == "R1A")
        # Do not substitute a different question's result snapshot.
        if (not board.get("published") or (r.code == "R1A" and not board.get("final")
                                          and board.get("after_question") != p)):
            board = None
        else:
            board = {k: board[k] for k in ("publication_id", "published_at", "after_question", "final", "data") if k in board}
            board["data"] = [{k: row[k] for k in ("rank", "team_id", "team_code", "team_name", "score") if k in row}
                             for row in board["data"][:16]]
    if view == "LEADERBOARD" and board is None:
        view = "RESULTS_PENDING"
    # Final publication may be projected without forcing the optional relay
    # review. Explicit review slides remain available to the host unchanged.
    combined = False
    if (a and b and a.status == b.status == "ENDED" and settings.leaderboard_visible
            and view in ("LEADERBOARD", "RESULTS_PENDING", "REVIEW_READY", "THANK_YOU")):
        from .combined_leaderboard import read_combined
        final_board = await read_combined(db)
        if final_board["published"]:
            board = final_board
            view, combined = "LEADERBOARD", True
    result = {"version": 1, "snapshot_at": now, "revision": revision,
              "view": view, "round_code": "R1" if combined else r.code if r else "R1A",
              "round_status": r.status if r else "DRAFT", "paused": bool(r and r.status == "PAUSED"),
              "question": question, "answer": answer, "leaderboard": board,
              "question_count": (round_rules(r).r1a.question_count if r and r.code == "R1A"
                                 else round_rules(r).r1b.turn_count if r else None),
              "deadline": slide.get("deadline"), "server_now": now,
              "remaining_ms": max(0, int((slide["deadline"] - (
                  r.paused_at if r.status == "PAUSED" and r.paused_at else now
              )).total_seconds() * 1000)) if slide.get("deadline") else None,
              "next_question_at": r.r1a_result_due_at if r and r.code == "R1A" else None}
    if admin:
        count = round_rules(b).r1b.turn_count if b else 0
        step = settings.review_step if b and settings.review_round_key == round_key(b) else -1
        result["controls"] = {"preview_upcoming": settings.preview_upcoming,
                              "leaderboard_visible": settings.leaderboard_visible}
        result["review"] = {"ended": bool(b and b.status == "ENDED"), "step": step,
                            "total_slides": count * 2,
                            "can_start": bool(b and b.status == "ENDED" and step < 0),
                            "can_next": bool(b and b.status == "ENDED" and 0 <= step < count * 2)}
    return result
