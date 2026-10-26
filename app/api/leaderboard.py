import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import LeaderboardSnapshot
from ..dependencies import require_player
from ..player_sessions import authorize_session

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

@router.get("/published")
async def published(round_code: str, user=Depends(require_player), db: AsyncSession=Depends(get_db)):
    if round_code not in ("R1A", "R1B"):
        raise HTTPException(400, "Unsupported round code")
    if user.get("sid"):
        await authorize_session(db, user, round_code)
    result = await db.execute(
        select(LeaderboardSnapshot).where(LeaderboardSnapshot.round_code==round_code)
        .order_by(LeaderboardSnapshot.published_at.desc()).limit(1)
    )
    snap = result.scalar_one_or_none()
    if not snap:
        return {"round_code": round_code, "published": False, "data": []}
    return {"round_code": round_code, "published": True, "publication_id": snap.id,
            "published_at": snap.published_at,
            "scope": {"round_code": round_code, "immutable": True},
            "data": json.loads(snap.data_json)[:16]}


@router.get("/mine")
async def my_published_rank(round_code: str, user=Depends(require_player), db: AsyncSession=Depends(get_db)):
    """Return only this team's standing from the latest immutable publication."""
    if round_code not in ("R1A", "R1B"):
        raise HTTPException(400, "Unsupported round code")
    if user.get("sid"):
        await authorize_session(db, user, round_code)
    snap = (await db.execute(
        select(LeaderboardSnapshot).where(LeaderboardSnapshot.round_code == round_code)
        .order_by(LeaderboardSnapshot.published_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not snap:
        return {"round_code": round_code, "published": False}
    row = next((item for item in json.loads(snap.data_json)
                if item["team_id"] == user["team_id"]), None)
    return {
        "round_code": round_code,
        "published": True,
        "publication_id": snap.id,
        "published_at": snap.published_at,
        "scope": {"round_code": round_code, "immutable": True},
        "rank": row["rank"] if row else None,
        "score": row["score"] if row else None,
    }
