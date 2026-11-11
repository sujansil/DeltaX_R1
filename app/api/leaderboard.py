from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..dependencies import require_player
from ..player_sessions import authorize_session
from ..question_reveal import read_leaderboard
from ..models import Round
from ..competition_rules import round_rules
from sqlalchemy import select

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

async def board(round_code, user, db):
    if round_code == "R1":
        from ..combined_leaderboard import read_combined
        await authorize_session(db, user, user.get("round_code"))
        return await read_combined(db, user["team_id"])
    if round_code not in ("R1A", "R1B"):
        raise HTTPException(400, "Unsupported round code")
    if user.get("sid"):
        await authorize_session(db, user, round_code)
    round_obj = await db.scalar(select(Round).where(Round.code == round_code))
    if not round_obj:
        raise HTTPException(404, "Round not found")
    rules = round_rules(round_obj)
    include = (rules.r1a.show_results_after_each_question if round_code == "R1A"
               else rules.r1b.show_results_after_each_question)
    return await read_leaderboard(db, round_code, user["team_id"],
                                  include_intermediate=include)

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


@router.get("/final")
async def final_round_one(db: AsyncSession = Depends(get_db)):
    from ..combined_leaderboard import read_combined
    return JSONResponse(jsonable_encoder(await read_combined(db)),
                        headers={"Cache-Control": "no-store"})
