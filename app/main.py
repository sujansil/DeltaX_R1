import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from .api import auth, r1a, r1b, admin, leaderboard, player
from .config import get_settings
from .db import SessionLocal
from .r1a_reconciliation import reconcile_r1a
from .r1b_finalization import reconcile_r1b_batch

settings = get_settings()
logger = logging.getLogger(__name__)


async def _r1a_reconciliation_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with SessionLocal() as db:
                # Avoid an app shutdown or a busy peer worker waiting indefinitely.
                await db.execute(text("SET LOCAL lock_timeout = '2s'"))
                await reconcile_r1a(db)
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("R1A reconciliation pass failed")
        try:
            async with SessionLocal() as db:
                await db.execute(text("SET LOCAL lock_timeout = '2s'"))
                await reconcile_r1b_batch(db)
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("R1B reconciliation pass failed")
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.r1a_reconciliation_interval_seconds
            )
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    task = asyncio.create_task(_r1a_reconciliation_loop(stop), name="r1a-reconciler")
    try:
        yield
    finally:
        stop.set()
        await task


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

Path(settings.media_root).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_root), name="media")
app.mount("/player", StaticFiles(directory="frontend/player", html=True), name="player")
app.mount("/admin-ui", StaticFiles(directory="frontend/admin", html=True), name="admin-ui")

app.include_router(auth.router)
app.include_router(r1a.router)
app.include_router(r1b.router)
app.include_router(admin.router)
app.include_router(leaderboard.router)
app.include_router(player.router)

@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    # Unique constraints are also the final concurrency guard. Do not leak SQL details.
    return JSONResponse(status_code=409, content={"detail": "The requested record already exists or conflicts with current state"})

@app.get("/")
async def root():
    return RedirectResponse(url="/player/")

@app.get("/health")
async def health():
    return {"status": "ok"}
