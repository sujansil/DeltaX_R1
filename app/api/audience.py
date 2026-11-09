from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audience as service
from ..db import get_db
from ..dependencies import require_admin

router = APIRouter(tags=["audience"])


@router.get("/api/public/audience")
async def public_state(db: AsyncSession = Depends(get_db)):
    return JSONResponse(jsonable_encoder(await service.snapshot(db)), headers={"Cache-Control": "no-store"})


@router.get("/api/admin/audience")
async def admin_state(user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return JSONResponse(jsonable_encoder(await service.snapshot(db, admin=True)), headers={"Cache-Control": "no-store"})


@router.put("/api/admin/audience")
async def update(data: service.SettingsChange, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await service.write_controls(db, user, data)


@router.post("/api/admin/audience/review")
async def review(data: service.ReviewChange, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await service.write_controls(db, user, data)
