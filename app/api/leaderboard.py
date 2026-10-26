from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..dependencies import require_player
from ..player_sessions import authorize_session
from ..question_reveal import read_leaderboard

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

async def board(round_code, user, db):
    if round_code not in ("R1A", "R1B"):
        raise HTTPException(400, "Unsupported round code")
    if user.get("sid"):
        await authorize_session(db, user, round_code)
    return await read_leaderboard(db, round_code, user["team_id"])

@router.get("/published")
async def published(round_code: str, user=Depends(require_player), db: AsyncSession=Depends(get_db)):
    return await board(round_code, user, db)

@router.get("/mine")
async def my_published_rank(round_code: str, user=Depends(require_player), db: AsyncSession=Depends(get_db)):
    result = await board(round_code, user, db)
    mine = result.pop("mine", None)
    result.pop("data", None)
    return {**result, "rank": mine["rank"] if mine else None,
            "score": mine["score"] if mine else None}
