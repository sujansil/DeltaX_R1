"""Final Round 1 standings, calculated from the existing score-event ledger.

Subround scorers remain the sole source of points. Publication freezes both the
standings and the qualifying roster in the existing snapshot store. All writers
take R1A then R1B locks, matching global reset and team/session transitions.
"""
from __future__ import annotations

import json
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import case, exists, func, select, union

from .models import (LeaderboardSnapshot, R1AReceipt, R1BReceipt, Round,
                     RoundEnrollment, ScoreEvent, Submission, Team, TeamRoundState)


COMBINED_KIND = "COMBINED_R1_FINAL_V1"
QUALIFIER_LIMIT = 16
TIE_POLICY = "UNRESOLVED"
TIE_POLICY_MESSAGE = (
    "Equal totals share rank. Team code orders the display only; no competition "
    "tie-break policy is configured. A tie crossing the 16-team cutoff blocks "
    "R2 qualifier selection."
)


def fail(code: str, message: str, status: int = 409):
    raise HTTPException(status, {"code": code, "message": message})


async def lock_rounds(db, *, exclusive: bool):
    rounds = (await db.scalars(select(Round).where(
        Round.code.in_(("R1A", "R1B"))
    ).order_by(Round.code).with_for_update(read=not exclusive))).all()
    return {r.code: r for r in rounds}


def ready(rounds: dict) -> bool:
    return (set(rounds) == {"R1A", "R1B"}
            and all(r.status == "ENDED" for r in rounds.values()))


def rank_rows(rows: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Stable display order is deliberately not a competition tie-break rule."""
    rows = sorted(rows, key=lambda row: (-row["score"], row["team_code"], row["team_id"]))
    previous_score, rank = None, 0
    by_score = defaultdict(list)
    for position, row in enumerate(rows, 1):
        if previous_score is None or previous_score != row["score"]:
            rank = position
        row["rank"] = rank
        previous_score = row["score"]
        by_score[row["score"]].append(row)
    ties = [{"rank": group[0]["rank"], "score": score,
             "team_ids": [r["team_id"] for r in group]}
            for score, group in by_score.items() if len(group) > 1]
    cutoff_tie = (len(rows) > QUALIFIER_LIMIT
                  and rows[QUALIFIER_LIMIT - 1]["score"] == rows[QUALIFIER_LIMIT]["score"])
    cutoff_ids = ([r["team_id"] for r in by_score[rows[QUALIFIER_LIMIT - 1]["score"]]]
                  if cutoff_tie else [])
    qualification = {
        "limit": QUALIFIER_LIMIT,
        "status": "BLOCKED_CUTOFF_TIE" if cutoff_tie else "READY",
        "team_ids": [] if cutoff_tie else [r["team_id"] for r in rows[:QUALIFIER_LIMIT]],
        "cutoff_tie_team_ids": cutoff_ids,
    }
    return rows, ties, qualification


async def calculate_combined(db):
    # Eligibility is historical participation, not today's enabled flag or the
    # existence of a positive score. The union also covers older relay-only runs.
    codes = ("R1A", "R1B")
    participants = union(*(
        select(model.team_id).where(model.round_code.in_(codes))
        for model in (RoundEnrollment, TeamRoundState, Submission, ScoreEvent)
    )).subquery()
    totals = (select(
        ScoreEvent.team_id,
        func.sum(case((ScoreEvent.round_code == "R1A", ScoreEvent.points), else_=0)).label("r1a_score"),
        func.sum(case((ScoreEvent.round_code == "R1B", ScoreEvent.points), else_=0)).label("r1b_score"),
    ).where(ScoreEvent.round_code.in_(codes)).group_by(ScoreEvent.team_id).subquery())
    result = await db.execute(select(
        Team.id, Team.code, Team.name,
        func.coalesce(totals.c.r1a_score, 0).label("r1a_score"),
        func.coalesce(totals.c.r1b_score, 0).label("r1b_score"),
    ).join(participants, participants.c.team_id == Team.id)
      .outerjoin(totals, totals.c.team_id == Team.id))
    return rank_rows([
        {"team_id": r.id, "team_code": r.code, "team_name": r.name,
         "r1a_score": int(r.r1a_score), "r1b_score": int(r.r1b_score),
         "score": int(r.r1a_score) + int(r.r1b_score)} for r in result.all()
    ])


def unpublished() -> dict:
    return {"round_code": "R1", "published": False, "ready": False, "final": False,
            "data": [], "mine": None, "total_teams": 0,
            "tie_policy": TIE_POLICY, "tie_policy_message": TIE_POLICY_MESSAGE,
            "ties": [], "qualification": {"limit": QUALIFIER_LIMIT,
                "status": "NOT_PUBLISHED", "team_ids": [], "cutoff_tie_team_ids": []}}


def published_result(snapshot, *, team_id=None) -> dict:
    payload = json.loads(snapshot.data_json)
    if payload.get("kind") != COMBINED_KIND:
        fail("COMBINED_PUBLICATION_INVALID", "Combined publication requires administrator recovery", 503)
    rows = payload["rows"]
    return {"round_code": "R1", "published": True, "ready": True, "final": True,
            "publication_id": snapshot.id, "published_at": snapshot.published_at,
            "scope": {"round_code": "R1", "immutable": True},
            "data": rows, "total_teams": len(rows),
            "mine": next((r for r in rows if r["team_id"] == team_id), None),
            "tie_policy": payload["tie_policy"],
            "tie_policy_message": payload["tie_policy_message"],
            "ties": payload["ties"], "qualification": payload["qualification"]}


async def combined_snapshot(db):
    return await db.scalar(select(LeaderboardSnapshot).where(
        LeaderboardSnapshot.round_code == "R1"))


async def pending_results(db):
    # Official end fences admissions and drains receipts. Fail closed for an
    # inconsistent legacy/imported state rather than freeze incomplete totals.
    return bool(await db.scalar(select(
        exists(select(R1AReceipt.id).where(R1AReceipt.status == "PENDING"))
        | exists(select(R1BReceipt.id).where(R1BReceipt.status == "PENDING"))
    )))


async def read_combined(db, team_id=None):
    """Public/player reads never compute live totals or return an admin preview."""
    snapshot = await combined_snapshot(db)
    return published_result(snapshot, team_id=team_id) if snapshot else unpublished()


async def preview_combined(db):
    rounds = await lock_rounds(db, exclusive=False)
    snapshot = await combined_snapshot(db)
    if snapshot:
        return published_result(snapshot)
    result = unpublished()
    result["round_statuses"] = {code: r.status for code, r in rounds.items()}
    if not ready(rounds):
        return result
    if await pending_results(db):
        return {**result, "blocked_reason": "COMBINED_RESULTS_PENDING"}
    rows, ties, qualification = await calculate_combined(db)
    # The qualifying roster is authoritative only after explicit publication.
    qualification["status"] = ("BLOCKED_CUTOFF_TIE" if qualification["cutoff_tie_team_ids"] else "PREVIEW")
    qualification["team_ids"] = []
    return {**result, "ready": True, "data": rows, "total_teams": len(rows),
            "ties": ties, "qualification": qualification}


async def publish_combined(db):
    """Caller adds its audit event and commits the same transaction."""
    rounds = await lock_rounds(db, exclusive=True)
    if not ready(rounds):
        fail("FINAL_PUBLICATION_REQUIRES_BOTH_ENDED",
             "End both R1(a) and R1(b) before publishing final Round 1 standings")
    existing = await combined_snapshot(db)
    if existing:
        return existing, True
    if await pending_results(db):
        fail("COMBINED_RESULTS_PENDING", "Resolve pending subround submissions before final publication")
    rows, ties, qualification = await calculate_combined(db)
    snapshot = LeaderboardSnapshot(round_code="R1", data_json=json.dumps({
        "kind": COMBINED_KIND, "rows": rows,
        "tie_policy": TIE_POLICY, "tie_policy_message": TIE_POLICY_MESSAGE,
        "ties": ties, "qualification": qualification,
        "rounds": {code: {"started_at": r.started_at.isoformat() if r.started_at else None,
                          "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                          "rules_version": r.rules_version} for code, r in rounds.items()},
    }, separators=(",", ":")))
    db.add(snapshot)
    await db.flush()
    return snapshot, False


async def qualifying_teams(db):
    result = await read_combined(db)
    if not result["published"]:
        fail("COMBINED_NOT_PUBLISHED", "Publish final Round 1 standings before selecting R2 qualifiers")
    if result["qualification"]["status"] == "BLOCKED_CUTOFF_TIE":
        raise HTTPException(409, {"code": "R2_QUALIFICATION_TIE_UNRESOLVED",
            "message": TIE_POLICY_MESSAGE,
            "publication_id": result["publication_id"],
            "team_ids": result["qualification"]["cutoff_tie_team_ids"]})
    return {"publication_id": result["publication_id"], "published_at": result["published_at"],
            "source": "R1_COMBINED_PUBLICATION", **result["qualification"],
            "data": [r for r in result["data"]
                     if r["team_id"] in result["qualification"]["team_ids"]]}
