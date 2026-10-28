"""PICO background runs and effective-span reads."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Record
from app.schemas import EffectivePicoSpanRead, PicoRunAccepted, PicoRunRequest, RecordPicoRead
from app.services.pico_service import create_pico_run, get_effective_spans, run_pico_task


router = APIRouter(tags=["pico"])


@router.post("/pico/run", response_model=PicoRunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_pico_run(
    payload: PicoRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> PicoRunAccepted:
    """Queue offline PICO tagging for a query version."""

    run = await create_pico_run(db, payload.query_version_id)
    background_tasks.add_task(run_pico_task, run.id)
    return PicoRunAccepted(run_id=run.id, query_version_id=payload.query_version_id)


@router.get("/records/{record_id}/pico", response_model=RecordPicoRead)
async def get_record_pico(record_id: int, db: AsyncSession = Depends(get_db)) -> RecordPicoRead:
    """Return current machine spans after applying append-only human corrections."""

    record = await db.get(Record, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    spans = await get_effective_spans(db, record)
    return RecordPicoRead(
        record_id=record_id,
        spans=[EffectivePicoSpanRead(**span.__dict__) for span in spans],
    )
