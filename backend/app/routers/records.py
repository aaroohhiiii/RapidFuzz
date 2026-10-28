from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import QueryRecord, QueryVersion, Record
from app.schemas import RecordPage, RecordRead

router = APIRouter()


@router.get("/records", response_model=list[RecordRead])
async def list_records(db: AsyncSession = Depends(get_db)) -> list[RecordRead]:
    result = await db.execute(select(Record).order_by(Record.created_at.desc()))
    return list(result.scalars().all())


@router.get("/records/{record_id}", response_model=RecordRead)
async def get_record(record_id: int, db: AsyncSession = Depends(get_db)) -> RecordRead:
    record = await db.get(Record, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@router.get("/queries/{query_version_id}/records", response_model=RecordPage)
async def list_query_records(
    query_version_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RecordPage:
    if await db.get(QueryVersion, query_version_id) is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    total = await db.scalar(
        select(func.count()).select_from(QueryRecord).where(QueryRecord.query_version_id == query_version_id)
    )
    result = await db.execute(
        select(Record)
        .join(QueryRecord, QueryRecord.record_id == Record.id)
        .where(QueryRecord.query_version_id == query_version_id)
        .order_by(QueryRecord.rank_in_results.asc().nullslast(), QueryRecord.found_at.asc(), Record.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return RecordPage(items=list(result.scalars().all()), limit=limit, offset=offset, total=total or 0)
