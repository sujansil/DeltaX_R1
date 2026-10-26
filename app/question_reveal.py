"""Question-close score visibility and automatic R1A leaderboard snapshots.

No new schema: automatic snapshots use a versioned JSON envelope in the existing
snapshot text column. Legacy list snapshots (R1B/manual exports) remain readable.
"""
from __future__ import annotations

import json
from datetime import timedelta, timezone
from sqlalchemy import case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from .models import LeaderboardSnapshot, RoundEnrollment, ScoreEvent, Submission, Team

KIND = "R1A_QUESTION_REVEAL_V1"


def closed_position(round_obj, now) -> int:
    """Last question whose score may be visible; a global pause freezes time."""
    position = round_obj.current_position or 0
    if round_obj.status == "DRAFT" or not position:
        return 0
    if round_obj.status == "ENDED":
        # Early End closes access but preserves already accepted scores.
        return position
    deadline = (round_obj.question_started_at + timedelta(seconds=30)
                if round_obj.question_started_at else None)
    reference = round_obj.paused_at if round_obj.status == "PAUSED" else now
    if deadline is not None and reference is not None and reference >= deadline:
        return position
    return max(0, position - 1)


def natural_close(round_obj, now):
    """Return the current effective deadline only once it really elapsed."""
    if not round_obj.current_position or not round_obj.question_started_at:
        return None
    deadline = round_obj.question_started_at + timedelta(seconds=30)
    reference = (round_obj.paused_at if round_obj.status == "PAUSED" else
                 round_obj.ended_at if round_obj.status == "ENDED" else now)
    if round_obj.status == "DRAFT" or reference is None or reference < deadline:
        return None
    return deadline


def snapshot_payload(raw: str) -> dict:
    """Decode both old list snapshots and the new versioned envelope."""
    data = json.loads(raw)
    if isinstance(data, list):
        return {"rows": data, "after_question": None, "kind": "LEGACY"}
    if isinstance(data, dict) and data.get("kind") == KIND and isinstance(data.get("rows"), list):
        return data
    raise ValueError("Unsupported leaderboard snapshot format")


async def visible_total(db: AsyncSession, team_id: int, round_code: str, cutoff: int) -> int:
    query = select(func.coalesce(func.sum(ScoreEvent.points), 0)).where(
        ScoreEvent.team_id == team_id, ScoreEvent.round_code == round_code)
    if round_code == "R1A":
        query = query.outerjoin(Submission, Submission.id == ScoreEvent.submission_id).where(
            or_(ScoreEvent.submission_id.is_(None), Submission.question_position <= cutoff))
    return int(await db.scalar(query) or 0)


async def ensure_question_snapshot(db: AsyncSession, round_obj, now):
    """Caller MUST own the exclusive Round lock (same as reconciliation/reset).

    The lock makes the (question, exact effective deadline) key idempotent across
    workers without introducing new migration state that reset must understand.
    """
    deadline = natural_close(round_obj, now)
    if deadline is None:
        return None
    deadline_key = deadline.astimezone(timezone.utc).isoformat()
    meta = cast(LeaderboardSnapshot.data_json, JSONB)
    existing = await db.scalar(select(LeaderboardSnapshot).where(
        LeaderboardSnapshot.round_code == "R1A",
        func.jsonb_extract_path_text(meta, "kind") == KIND,
        func.jsonb_extract_path_text(meta, "after_question") == str(round_obj.current_position),
        func.jsonb_extract_path_text(meta, "question_deadline") == deadline_key,
    ).order_by(LeaderboardSnapshot.id.desc()).limit(1))
    if existing is not None:
        return existing
    await db.flush()
    eligible_points = case(
        (or_(ScoreEvent.submission_id.is_(None),
             Submission.question_position <= round_obj.current_position), ScoreEvent.points),
        else_=0,
    )
    total = func.coalesce(func.sum(eligible_points), 0)
    result = await db.execute(select(Team.id, Team.code, Team.name, total.label("score"))
        .join(RoundEnrollment, (RoundEnrollment.team_id == Team.id)
              & (RoundEnrollment.round_code == "R1A"))
        .outerjoin(ScoreEvent, (ScoreEvent.team_id == Team.id) & (ScoreEvent.round_code == "R1A"))
        .outerjoin(Submission, Submission.id == ScoreEvent.submission_id)
        .group_by(Team.id).order_by(total.desc(), Team.code.asc()))
    rows = [{"rank": rank, "team_id": row.id, "team_code": row.code,
             "team_name": row.name, "score": int(row.score)}
            for rank, row in enumerate(result.all(), 1)]
    snapshot = LeaderboardSnapshot(round_code="R1A", published_at=now, data_json=json.dumps({
        "kind": KIND, "after_question": round_obj.current_position,
        "question_deadline": deadline_key, "rows": rows,
    }))
    db.add(snapshot)
    round_obj.leaderboard_published_at = now
    await db.flush()
    return snapshot


async def read_leaderboard(db: AsyncSession, round_code: str, team_id: int | None = None):
    query = select(LeaderboardSnapshot).where(LeaderboardSnapshot.round_code == round_code)
    if round_code == "R1A":
        # Never expose a legacy/manual snapshot containing still-open answers.
        query = query.where(func.jsonb_extract_path_text(cast(LeaderboardSnapshot.data_json, JSONB), "kind") == KIND)
    snapshot = await db.scalar(query.order_by(LeaderboardSnapshot.published_at.desc(),
                                             LeaderboardSnapshot.id.desc()).limit(1))
    if snapshot is None:
        return {"round_code": round_code, "published": False, "data": [], "mine": None,
                "after_question": None, "total_teams": 0}
    payload = snapshot_payload(snapshot.data_json)
    rows = payload["rows"]
    mine = next((row for row in rows if row["team_id"] == team_id), None) if team_id else None
    return {"round_code": round_code, "published": True, "publication_id": snapshot.id,
            "published_at": snapshot.published_at, "after_question": payload.get("after_question"),
            "question_deadline": payload.get("question_deadline"),
            "automatic": payload.get("kind") == KIND, "total_teams": len(rows),
            "scope": {"round_code": round_code, "immutable": True}, "data": rows[:16], "mine": mine}
