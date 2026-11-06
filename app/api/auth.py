from datetime import timedelta
import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..dependencies import require_player
from ..models import ActivationThrottle, Admin, PlayerDevice, Team
from ..player_sessions import ROUND_CAPACITY, authorize_session, lease_expiry, lock_login_round
from ..security import create_token, hash_password_async, verify_password_async
from ..timing import utc_now

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class TeamCreatePasswordIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    activation_token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


ACTIVATION_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def normalize_activation_code(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch not in " -")


def format_activation_code(value: str) -> str:
    normalized = normalize_activation_code(value)
    return f"{normalized[:4]}-{normalized[4:]}"


def activation_hash(token: str) -> str:
    """Key the digest so an 8-character code cannot be brute-forced from a DB dump."""
    payload = b"activation-code:" + normalize_activation_code(token).encode()
    return hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).hexdigest()


def legacy_activation_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_activation_token() -> tuple[str, str]:
    code = "".join(secrets.choice(ACTIVATION_ALPHABET) for _ in range(8))
    return format_activation_code(code), activation_hash(code)


def throttle_digest(username: str) -> str:
    payload = b"activation-throttle:" + username.encode()
    return hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).hexdigest()


async def locked_throttle(db: AsyncSession, username: str, now):
    digest = throttle_digest(username)
    await db.execute(insert(ActivationThrottle).values(
        identifier_digest=digest, window_started_at=now, updated_at=now,
    ).on_conflict_do_nothing(index_elements=[ActivationThrottle.identifier_digest]))
    return await db.scalar(select(ActivationThrottle).where(
        ActivationThrottle.identifier_digest == digest).with_for_update())


def record_activation_failure(row: ActivationThrottle, now) -> None:
    window = timedelta(minutes=settings.activation_attempt_window_minutes)
    if now - row.window_started_at >= window:
        row.failed_attempts = 0
        row.window_started_at = now
        row.locked_until = None
    row.failed_attempts += 1
    row.updated_at = now
    if row.failed_attempts >= settings.activation_failed_attempt_limit:
        row.locked_until = now + timedelta(minutes=settings.activation_lockout_minutes)


@router.post("/team/login")
async def team_login(data: LoginIn, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        # Global lock order is Round -> Team -> Session.
        round_obj = await lock_login_round(db)
        team = (await db.execute(select(Team).where(
            Team.login_username == data.username, Team.enabled.is_(True)
        ).with_for_update())).scalar_one_or_none()
        if not team or not team.password_hash or not await verify_password_async(data.password, team.password_hash):
            raise HTTPException(401, "Invalid team credentials")

        now = utc_now()
        await db.execute(update(PlayerDevice).where(
            PlayerDevice.team_id == team.id,
            PlayerDevice.round_code == round_obj.code,
            PlayerDevice.active.is_(True),
            PlayerDevice.expires_at <= now,
        ).values(active=False))
        active_slots = set((await db.scalars(select(PlayerDevice.player_number).where(
            PlayerDevice.team_id == team.id,
            PlayerDevice.round_code == round_obj.code,
            PlayerDevice.active.is_(True),
        ))).all())
        # player_number is retained as an authenticated-device claim for JWT
        # compatibility. It is no longer the physical relay-player position.
        slot = next((n for n in range(1, ROUND_CAPACITY[round_obj.code] + 1)
                     if n not in active_slots), None)
        if slot is None:
            raise HTTPException(409, f"{round_obj.code} session capacity is full")

        session_id = secrets.token_hex(32)
        expires_at = lease_expiry(now)
        db.add(PlayerDevice(team_id=team.id, player_number=slot, round_code=round_obj.code,
                            session_id=session_id, expires_at=expires_at, last_seen_at=now))

    return {
        "access_token": create_token(team.code, "team", team.id, session_id, round_obj.code, slot),
        "team_code": team.code,
        "team_name": team.name,
        "round_code": round_obj.code,
        "slot": slot,
        "lease_expires_at": expires_at,
        "heartbeat_interval_seconds": settings.player_heartbeat_seconds,
    }


@router.post("/team/logout")
async def team_logout(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    async with db.begin():
        device = await authorize_session(db, user, user["round_code"], lock=True)
        device.active = False
    return {"ok": True}


@router.post("/team/heartbeat")
async def team_heartbeat(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    async with db.begin():
        device = await authorize_session(db, user, user["round_code"], lock=True)
        now = utc_now()
        device.last_seen_at = now
        device.expires_at = lease_expiry(now)
    return {"ok": True, "last_seen_at": device.last_seen_at,
            "lease_expires_at": device.expires_at}


@router.post("/team/departing")
async def team_departing(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    """Best-effort unload hint; a quick refresh heartbeat cancels the short lease."""
    async with db.begin():
        device = await authorize_session(db, user, user["round_code"], lock=True)
        now = utc_now()
        device.expires_at = min(device.expires_at, now + timedelta(seconds=10))
    return {"ok": True, "lease_expires_at": device.expires_at}


@router.get("/team/me")
async def team_me(user=Depends(require_player), db: AsyncSession = Depends(get_db)):
    device = await authorize_session(db, user, user["round_code"])
    return {"team_id": device.team_id, "team_code": user["sub"],
            "session_id": device.session_id, "round_code": device.round_code,
            "slot": device.player_number, "lease_expires_at": device.expires_at,
            "token_expires_at": user.get("exp"),
            "heartbeat_interval_seconds": settings.player_heartbeat_seconds}


@router.post("/team/set-password")
async def team_set_password(data: TeamCreatePasswordIn, db: AsyncSession = Depends(get_db)):
    rejected = False
    async with db.begin():
        now = utc_now()
        throttle = await locked_throttle(db, data.username, now)
        team = (await db.execute(select(Team).where(
            Team.login_username == data.username
        ).with_for_update())).scalar_one_or_none()
        normalized = normalize_activation_code(data.activation_token)
        code_well_formed = len(normalized) == 8 and all(c in ACTIVATION_ALPHABET for c in normalized)
        supplied_hashes = {legacy_activation_hash(data.activation_token)}
        if code_well_formed:
            supplied_hashes.add(activation_hash(normalized))
        valid = (
            not (throttle.locked_until and throttle.locked_until > now)
            and team is not None
            and team.password_hash is None
            and team.activation_token_hash is not None
            and any(secrets.compare_digest(team.activation_token_hash, candidate)
                    for candidate in supplied_hashes)
            and team.activation_expires_at is not None
            and team.activation_expires_at > now
        )
        if not valid:
            record_activation_failure(throttle, now)
            rejected = True
        else:
            team.password_hash = await hash_password_async(data.new_password)
            team.activation_token_hash = None
            team.activation_expires_at = None
            await db.execute(delete(ActivationThrottle).where(
                ActivationThrottle.identifier_digest == throttle.identifier_digest))
    if rejected:
        raise HTTPException(400, "Invalid or expired activation details")
    return {"ok": True}


class AdminSetupIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)


@router.post("/admin/setup")
async def admin_setup(data: AdminSetupIn, db: AsyncSession = Depends(get_db)):
    """Development provisioning with JSON credentials; never a password reset."""
    if settings.environment != "development":
        raise HTTPException(404, "Development admin setup is disabled")
    username = data.username.strip()
    if not username:
        raise HTTPException(422, "Admin username cannot be blank")
    existing = await db.scalar(select(Admin.id).where(Admin.username == username))
    if existing is not None:
        raise HTTPException(409, "Admin already exists; its password was not changed")
    hashed = await hash_password_async(data.password)
    db.add(Admin(username=username, password_hash=hashed))
    await db.commit()
    return {"created": True, "username": username}


@router.post("/admin/login")
async def admin_login(data: LoginIn, db: AsyncSession = Depends(get_db)):
    admin = (await db.execute(select(Admin).where(
        Admin.username == data.username, Admin.enabled.is_(True)
    ))).scalar_one_or_none()
    if not admin or not await verify_password_async(data.password, admin.password_hash):
        raise HTTPException(401, "Invalid admin credentials")
    return {"access_token": create_token(admin.username, "admin")}
