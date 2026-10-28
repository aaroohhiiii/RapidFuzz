from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import QueryHit, QueryVersion, RunLog
from app.schemas import (
    QueryActivationRead, QueryCountResponse, QueryDiffRead, QueryHitRead,
    QueryLineageRead, QuerySnapshotRead, QueryVersionCreate, QueryVersionRead,
)
from app.services import europepmc, pubmed
from app.services.loop_service import activate, diff, lineage, snapshot

router = APIRouter()


@router.post("/queries", response_model=QueryVersionRead)
async def create_query_version(payload: QueryVersionCreate, db: AsyncSession = Depends(get_db)) -> QueryVersionRead:
    version = QueryVersion(
        label=payload.label,
        query_text=payload.query_text,
        parent_id=payload.parent_id,
        query_struct=payload.query_struct,
        date_from=payload.date_from,
        date_to=payload.date_to,
        created_by=payload.created_by,
        status="draft",
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    return version


@router.get("/queries", response_model=list[QueryVersionRead])
async def list_query_versions(db: AsyncSession = Depends(get_db)) -> list[QueryVersionRead]:
    result = await db.execute(select(QueryVersion).order_by(QueryVersion.created_at.desc()))
    return result.scalars().all()


@router.get("/queries/{query_version_id}", response_model=QueryVersionRead)
async def get_query_version(query_version_id: int, db: AsyncSession = Depends(get_db)) -> QueryVersionRead:
    version = await db.get(QueryVersion, query_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    return version


@router.get("/queries/{query_version_id}/hits", response_model=list[QueryHitRead])
async def get_query_hits(
    query_version_id: int, db: AsyncSession = Depends(get_db)
) -> list[QueryHitRead]:
    """Return the latest persisted source counts for one query version."""

    if await db.get(QueryVersion, query_version_id) is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    return list((await db.scalars(
        select(QueryHit).where(QueryHit.query_version_id == query_version_id).order_by(QueryHit.source)
    )).all())


@router.post("/queries/{query_version_id}/count", response_model=QueryCountResponse)
async def count_hits_for_query_version(query_version_id: int, db: AsyncSession = Depends(get_db)) -> QueryCountResponse:
    version = await db.get(QueryVersion, query_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Query version not found")

    run = RunLog(
        query_version_id=query_version_id,
        step="count",
        params={"query_text": version.query_text, "date_from": version.date_from.isoformat() if version.date_from else None, "date_to": version.date_to.isoformat() if version.date_to else None},
        status="running",
    )
    db.add(run)
    await db.flush()

    pubmed_count = await pubmed.count_hits(version.query_text, version.date_from, version.date_to)
    epmc_count = await europepmc.count_hits(version.query_text, version.date_from, version.date_to)
    hits = {
        "pubmed": pubmed_count,
        "europepmc": epmc_count,
    }

    for source, hit_count in hits.items():
        stmt = insert(QueryHit).values(query_version_id=query_version_id, source=source, hit_count=hit_count).on_conflict_do_update(
            index_elements=["query_version_id", "source"],
            set_={"hit_count": hit_count},
        )
        await db.execute(stmt)

    run.stats = hits
    run.status = "done"
    run.finished_at = __import__("datetime").datetime.utcnow()
    await db.commit()
    return QueryCountResponse(query_version_id=query_version_id, hits=hits)


@router.post("/queries/{query_version_id}/approve", response_model=QueryVersionRead)
async def approve_query_version(query_version_id: int, db: AsyncSession = Depends(get_db)) -> QueryVersionRead:
    version = await db.get(QueryVersion, query_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    version.status = "approved"
    await db.commit()
    await db.refresh(version)
    return version


@router.get("/queries/{query_version_id}/lineage", response_model=QueryLineageRead)
async def get_query_lineage(
    query_version_id: int, db: AsyncSession = Depends(get_db)
) -> QueryLineageRead:
    return QueryLineageRead(**await lineage(db, query_version_id))


@router.get("/queries/{query_version_id}/snapshot", response_model=QuerySnapshotRead)
async def get_query_snapshot(
    query_version_id: int, db: AsyncSession = Depends(get_db)
) -> QuerySnapshotRead:
    return QuerySnapshotRead(**await snapshot(db, query_version_id))


@router.get("/queries/{query_version_id}/diff", response_model=QueryDiffRead)
async def get_query_diff(
    query_version_id: int, against: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> QueryDiffRead:
    return QueryDiffRead(**await diff(db, query_version_id, against))


@router.post("/queries/{query_version_id}/activate", response_model=QueryActivationRead)
async def activate_query_version(
    query_version_id: int, db: AsyncSession = Depends(get_db)
) -> QueryActivationRead:
    run = await activate(db, query_version_id)
    return QueryActivationRead(active_query_version_id=query_version_id, run_id=run.id)
