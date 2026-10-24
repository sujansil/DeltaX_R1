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
        raise HTTPException(403, "Admin access required")
    return user

async def require_player(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    if user.get("role") != "team":
        raise HTTPException(403, "Team access required")
    session = (await db.execute(select(PlayerDevice).where(
        PlayerDevice.team_id == user.get("team_id"),
        PlayerDevice.session_id == user.get("sid"),
        PlayerDevice.active.is_(True),
        PlayerDevice.expires_at > utc_now(),
    ))).scalar_one_or_none()
    if not session:
        raise HTTPException(401, "This team session is no longer active")
    return user
