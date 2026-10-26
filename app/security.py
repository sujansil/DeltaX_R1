from datetime import datetime, timedelta, timezone
import uuid
import jwt
from pwdlib import PasswordHash
from fastapi import HTTPException, status
from starlette.concurrency import run_in_threadpool
from .config import get_settings

password_hash = PasswordHash.recommended()
settings = get_settings()

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)

async def hash_password_async(password: str) -> str:
    return await run_in_threadpool(hash_password, password)

async def verify_password_async(password: str, hashed: str) -> bool:
    return await run_in_threadpool(verify_password, password, hashed)

def create_token(subject: str, role: str, team_id: int | None = None,
                 session_id: str | None = None, round_code: str | None = None,
                 slot: int | None = None):
    payload = {
        "sub": subject,
        "role": role,
        "team_id": team_id,
        "sid": session_id or uuid.uuid4().hex,
        "round_code": round_code,
        "slot": slot,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"code": "TOKEN_INVALID",
                                    "message": "Invalid or expired session"})
