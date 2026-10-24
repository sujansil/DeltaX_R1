from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from .api import auth, r1a, r1b, admin, leaderboard
from .config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0")

Path(settings.media_root).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_root), name="media")
app.mount("/player", StaticFiles(directory="frontend/player", html=True), name="player")
app.mount("/admin-ui", StaticFiles(directory="frontend/admin", html=True), name="admin-ui")

app.include_router(auth.router)
app.include_router(r1a.router)
app.include_router(r1b.router)
app.include_router(admin.router)
app.include_router(leaderboard.router)

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
