from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import RunLog
from app.schemas import FetchRequest, FetchResponse, RunLogRead
from app.services.fetch_service import create_fetch_run, run_fetch_task

router = APIRouter()


@router.post("/runs/fetch", response_model=FetchResponse, status_code=status.HTTP_202_ACCEPTED)
async def fetch_records_for_query(payload: FetchRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)) -> FetchResponse:
    run = await create_fetch_run(
        db, payload.query_version_id, payload.source, payload.query_version_ids
    )
    background_tasks.add_task(run_fetch_task, run.id)
    source = payload.source or "all"
    return FetchResponse(
        query_version_id=payload.query_version_id,
        source=source,
        run_id=run.id,
    )


@router.get("/runs/{run_id}", response_model=RunLogRead)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)) -> RunLogRead:
    run = await db.get(RunLog, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs", response_model=list[RunLogRead])
async def list_runs(
    query_version_id: int | None = None,
    step: str | None = Query(default=None, pattern="^(count|fetch|dedup|screen|pico|export)$"),
    db: AsyncSession = Depends(get_db),
) -> list[RunLogRead]:
    """List run-log rows newest first, optionally scoped by version and step."""

    statement = select(RunLog)
    if query_version_id is not None:
        statement = statement.where(RunLog.query_version_id == query_version_id)
    if step is not None:
        statement = statement.where(RunLog.step == step)
    return list((await db.scalars(statement.order_by(RunLog.id.desc()))).all())
