from datetime import timedelta
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..dependencies import require_player
from ..models import Admin, PlayerDevice, Team
from ..security import create_token, hash_password_async, verify_password_async
from ..timing import utc_now

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class TeamCreatePasswordIn(BaseModel):
    team_code: str = Field(min_length=1, max_length=32)
    activation_token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


def activation_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_activation_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, activation_hash(token)


@router.post("/team/login")
async def team_login(data: LoginIn, db: AsyncSession = Depends(get_db)):
    # Locking the team row makes the one-session check atomic across API workers.
    async with db.begin():
        team = (await db.execute(select(Team).where(
            Team.login_username == data.username, Team.enabled.is_(True)
        ).with_for_update())).scalar_one_or_none()
        if not team or not team.password_hash or not await verify_password_async(data.password, team.password_hash):
            raise HTTPException(401, "Invalid team credentials")

        now = utc_now()
        device = (await db.execute(select(PlayerDevice).where(
            PlayerDevice.team_id == team.id
        ).with_for_update())).scalar_one_or_none()
        if device and device.active and device.expires_at > now:
            raise HTTPException(409, "This team already has an active login")

        session_id = secrets.token_hex(32)
        expires_at = now + timedelta(minutes=settings.access_token_minutes)
        if device:
            device.session_id = session_id
            device.active = True
            device.created_at = now
            device.expires_at = expires_at
            device.last_seen_at = now
        else:
            db.add(PlayerDevice(team_id=team.id, player_number=1,
                                session_id=session_id, expires_at=expires_at,
                                last_seen_at=now))

    return {
        "access_token": create_token(team.code, "team", team.id, session_id),
        "team_code": team.code,
        "team_name": team.name,
    }


@router.post("/team/logout")
async def team_logout(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    device = (await db.execute(select(PlayerDevice).where(
        PlayerDevice.team_id == user["team_id"], PlayerDevice.session_id == user["sid"]
    ))).scalar_one_or_none()
    if device:
        device.active = False
        await db.commit()
    return {"ok": True}


@router.post("/team/heartbeat")
async def team_heartbeat(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    device = (await db.execute(select(PlayerDevice).where(
        PlayerDevice.team_id == user["team_id"], PlayerDevice.session_id == user["sid"]
    ))).scalar_one()
    device.last_seen_at = utc_now()
    await db.commit()
    return {"ok": True, "last_seen_at": device.last_seen_at}


@router.post("/team/set-password")
async def team_set_password(data: TeamCreatePasswordIn, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        team = (await db.execute(select(Team).where(
            Team.code == data.team_code
        ).with_for_update())).scalar_one_or_none()
        now = utc_now()
        valid = (
            team is not None
            and team.password_hash is None
            and team.activation_token_hash is not None
            and secrets.compare_digest(team.activation_token_hash, activation_hash(data.activation_token))
            and team.activation_expires_at is not None
            and team.activation_expires_at > now
        )
        if not valid:
            raise HTTPException(400, "Invalid or expired activation details")
        team.password_hash = await hash_password_async(data.new_password)
        team.activation_token_hash = None
        team.activation_expires_at = None
    return {"ok": True}


@router.post("/admin/login")
async def admin_login(data: LoginIn, db: AsyncSession = Depends(get_db)):
    admin = (await db.execute(select(Admin).where(
        Admin.username == data.username, Admin.enabled.is_(True)
    ))).scalar_one_or_none()
    if not admin or not await verify_password_async(data.password, admin.password_hash):
        raise HTTPException(401, "Invalid admin credentials")
    return {"access_token": create_token(admin.username, "admin")}
