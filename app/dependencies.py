from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .security import decode_token
from .db import get_db
from .models import PlayerDevice
from .timing import utc_now
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

bearer = HTTPBearer()

def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    return decode_token(creds.credentials)

def require_admin(user=Depends(current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, {"code": "ADMIN_REQUIRED", "message": "Admin access required"})
    return user

async def require_player(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    if user.get("role") != "team":
        raise HTTPException(403, {"code": "PLAYER_REQUIRED", "message": "Team access required"})
    if user.get("team_id") is None or user.get("sid") is None:
        raise HTTPException(401, {"code": "SESSION_INVALID", "message": "Invalid player session"})
    # Compatibility for pre-migration JWTs: the persisted session remains the
    # authority and supplies the newly introduced round/slot claims.
    if user.get("round_code") is None or user.get("slot") is None:
        session = (await db.execute(select(PlayerDevice).where(
            PlayerDevice.team_id == user["team_id"],
            PlayerDevice.session_id == user["sid"],
            PlayerDevice.active.is_(True),
            PlayerDevice.revoked_at.is_(None),
            PlayerDevice.expires_at > utc_now(),
        ))).scalar_one_or_none()
        if not session:
            raise HTTPException(401, {"code": "SESSION_INACTIVE",
                                      "message": "Session expired or revoked; log in again"})
        user = {**user, "round_code": session.round_code, "slot": session.player_number}
    return user
