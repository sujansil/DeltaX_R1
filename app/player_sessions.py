"""Server-owned player-session lifecycle and authorization helpers."""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import PlayerDevice, Round, RoundStatus, Team
from .timing import utc_now

settings = get_settings()
ROUND_CAPACITY = {"R1A": 1, "R1B": 1}


async def lock_login_round(db: AsyncSession) -> Round:
    """Pick scope from competition state, never from login input.

    R1B becomes the login scope once it is live/paused. Otherwise R1A owns the
    event until it ends, after which pre-open R1B is the scope.
    """
    rounds = (await db.scalars(select(Round).where(
        Round.code.in_(("R1A", "R1B"))
    ).order_by(Round.code).with_for_update())).all()
    by_code = {r.code: r for r in rounds}
    r1b = by_code.get("R1B")
    r1a = by_code.get("R1A")
    if r1b and r1b.status in (RoundStatus.LIVE.value, RoundStatus.PAUSED.value):
        return r1b
    if r1a and r1a.status != RoundStatus.ENDED.value:
        return r1a
    if r1b and r1b.status != RoundStatus.ENDED.value:
        return r1b
    raise HTTPException(409, "No Round 1 player login is currently available")


async def authorize_session(db: AsyncSession, user: dict, round_code: str, *, lock: bool = False):
    if user.get("round_code") != round_code:
        raise HTTPException(403, {"code": "SESSION_SCOPE_MISMATCH",
                                  "message": f"This session is scoped to {user.get('round_code') or 'another round'}"})
    statement = select(PlayerDevice).where(
        PlayerDevice.session_id == user.get("sid"),
        PlayerDevice.team_id == user.get("team_id"),
        PlayerDevice.round_code == round_code,
        PlayerDevice.player_number == user.get("slot"),
        PlayerDevice.active.is_(True),
    )
    if lock:
        statement = statement.with_for_update(read=True)
    session = (await db.execute(statement)).scalar_one_or_none()
    now = utc_now()
    if not session or session.revoked_at is not None or session.expires_at <= now:
        raise HTTPException(401, {"code": "SESSION_INACTIVE",
                                  "message": "Session expired or revoked; log in again"})
    return session


async def lock_team(db: AsyncSession, team_id: int):
    return (await db.execute(select(Team).where(Team.id == team_id).with_for_update())).scalar_one()


def lease_expiry(now):
    return now + timedelta(seconds=settings.player_session_lease_seconds)
