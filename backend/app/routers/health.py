from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    try:
        await db.execute(text("SELECT 1"))
        return HealthResponse(status="ok")
    except Exception as exc:  # pragma: no cover - DB is expected to exist in runtime
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
