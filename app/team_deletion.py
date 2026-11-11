"""Fail-closed eligibility checks for permanent deletion of unused teams.

The caller owns one transaction through deletion and audit insertion. Keep the
existing mutation order: R1A -> R1B -> Team -> PlayerDevice. Round locks fence
enrollment, gameplay, reconciliation, publication and rehearsal reset; the team
lock also fences login, activation, edits and new foreign-key references.
"""
import json

from fastapi import HTTPException
from sqlalchemy import exists, select

from .models import (AdminAction, LeaderboardSnapshot, PlayerDevice, R1AReceipt,
                     R1BDraft, R1BReceipt, Round, RoundEnrollment, ScoreEvent,
                     Submission, Team, TeamRoundState)


def blocked(code, message):
    raise HTTPException(409, {"code": code, "message": message})


def snapshot_references_team(raw, team):
    """Read all persisted formats, including legacy lists and qualification IDs.

    Malformed or unknown snapshots must never become permission to erase history.
    Team names alone are not identities (names need not be unique).
    """
    payload = json.loads(raw)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and payload.get("kind") in {
        "R1A_QUESTION_REVEAL_V1", "R1B_TURN_REVEAL_V1",
        "EXPLICIT_FINAL_PUBLICATION_V1", "COMBINED_R1_FINAL_V1",
    }:
        rows = payload["rows"]
    else:
        raise ValueError("Unsupported snapshot format")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not str(row.get("team_id", "")).isdigit()
        for row in rows
    ):
        raise ValueError("Snapshot rows lack team identities")

    def references(value):
        if isinstance(value, list):
            return any(references(item) for item in value)
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "team_id" and str(item) == str(team.id):
                    return True
                if key in {"team_ids", "cutoff_tie_team_ids"}:
                    if not isinstance(item, list):
                        raise ValueError("Invalid qualification identities")
                    if any(str(identity) == str(team.id) for identity in item):
                        return True
                if key == "team_code" and item == team.code:
                    return True
                if references(item):
                    return True
        return False

    return references(payload)


async def lock_unused_team(db, team_id, confirmation):
    await db.execute(select(Round).where(Round.code.in_(("R1A", "R1B")))
                     .order_by(Round.code).with_for_update())
    team = await db.scalar(select(Team).where(Team.id == team_id).with_for_update())
    if team is None:
        raise HTTPException(404, {"code": "TEAM_NOT_FOUND", "message": "Team not found"})
    if confirmation != team.name:
        raise HTTPException(422, {"code": "TEAM_CONFIRMATION_INVALID",
                                  "message": "Type the exact current team name to confirm deletion"})

    # Heartbeat/logout lock session rows independently of round/team rows. Lock
    # them too before checking active status, including renewable expired leases.
    sessions = (await db.scalars(select(PlayerDevice).where(
        PlayerDevice.team_id == team_id).order_by(PlayerDevice.id).with_for_update())).all()
    if any(session.active for session in sessions):
        blocked("TEAM_HAS_ACTIVE_SESSIONS", "Revoke all team sessions before deleting an unused team")

    for model in (RoundEnrollment, TeamRoundState, Submission, ScoreEvent,
                  R1BDraft, R1AReceipt, R1BReceipt):
        if await db.scalar(select(exists().where(model.team_id == team_id))):
            blocked("TEAM_HAS_COMPETITION_HISTORY",
                    f"Team cannot be deleted: competition history exists in {model.__tablename__}")

    # Reset deliberately removes competition rows and publications, but its
    # durable audit contains counts rather than team identities. Absence of rows
    # cannot prove that a team predating such a reset has never participated.
    if await db.scalar(select(exists().where(
        AdminAction.action == "RESET_ROUND_ONE", AdminAction.created_at >= team.created_at
    ))):
        blocked("TEAM_HISTORY_UNVERIFIABLE",
                "Team predates a recorded Round 1 reset; historical nonparticipation cannot be verified")

    snapshots = await db.scalars(select(LeaderboardSnapshot.data_json))
    for raw in snapshots:
        try:
            referenced = snapshot_references_team(raw, team)
        except (ValueError, TypeError, KeyError, RecursionError):
            blocked("TEAM_HISTORY_UNVERIFIABLE",
                    "A stored publication cannot be verified; team deletion is blocked")
        if referenced:
            blocked("TEAM_HAS_PUBLISHED_RESULTS",
                    "Team is referenced in a published leaderboard or qualification snapshot")
    return team
