import json
import os
import uuid
from decimal import Decimal
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from .config import get_settings
from .models import *
from .timing import utc_now, elapsed_ms, remaining_ms
from .scoring import r1a_result, numeric_correct

settings = get_settings()

class StorageService:
    def __init__(self):
        self.root = Path(settings.media_root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def save(self, upload, prefix: str) -> str:
        ext = Path(upload.filename or "").suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("Only PNG/JPG images are allowed")
        folder = self.root / prefix
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{ext}"
        path = folder / name
        total = 0
        try:
            with path.open("wb") as f:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise ValueError(f"Image exceeds {settings.max_upload_bytes // (1024 * 1024)} MB")
                    f.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return f"{settings.media_public_prefix}/{prefix}/{name}"

class LeaderboardService:
    async def calculate(self, db: AsyncSession, round_code: str):
        q = await db.execute(
            select(Team.id, Team.code, Team.name, func.coalesce(func.sum(ScoreEvent.points), 0).label("score"))
            .outerjoin(ScoreEvent, (ScoreEvent.team_id == Team.id) & (ScoreEvent.round_code == round_code))
            .where(Team.enabled.is_(True))
            .group_by(Team.id)
            .order_by(func.coalesce(func.sum(ScoreEvent.points), 0).desc(), Team.code.asc())
        )
        return [{"rank": i, "team_id": r.id, "team_code": r.code, "team_name": r.name, "score": int(r.score)}
                for i, r in enumerate(q.all(), start=1)]

    async def publish(self, db: AsyncSession, round_code: str):
        data = await self.calculate(db, round_code)
        snap = LeaderboardSnapshot(round_code=round_code, data_json=json.dumps(data))
        db.add(snap)
        await db.flush()
        return data

async def get_round(db, code: str) -> Round:
    result = await db.execute(select(Round).where(Round.code == code))
    obj = result.scalar_one_or_none()
    if not obj:
        raise ValueError(f"Round {code} not found")
    return obj

async def r1a_current_question(db, position: int):
    result = await db.execute(
        select(Question).where(Question.round_code=="R1A", Question.position==position, Question.enabled.is_(True))
    )
    return result.scalar_one_or_none()

async def r1b_state(db, team_id: int):
    result = await db.execute(select(TeamRoundState).where(
        TeamRoundState.team_id==team_id, TeamRoundState.round_code=="R1B"
    ))
    return result.scalar_one_or_none()

async def ensure_r1b_state(db, team_id: int):
    state = await r1b_state(db, team_id)
    if state:
        return state
    state = TeamRoundState(team_id=team_id, round_code="R1B", current_player=1,
                           current_question_position=1, turn_status="WAITING")
    db.add(state)
    await db.flush()
    return state
