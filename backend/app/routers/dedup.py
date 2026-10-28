"""Deduplication run, review, and human-override endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import DedupGroup, DedupPair, GroupMember, QueryRecord, Record, RunLog
from app.schemas import (
    DedupGroupMemberRead,
    DedupGroupPage,
    DedupGroupRead,
    DedupPairEvidence,
    DedupPairPage,
    DedupPairRead,
    DedupRecordSummary,
    DedupRunAccepted,
    DedupRunRequest,
    DedupVerdictRequest,
    DedupVerdictResponse,
)
from app.services.dedup_service import apply_human_verdict, create_dedup_run, run_dedup_task


router = APIRouter(prefix="/dedup", tags=["dedup"])


@router.post("/run", response_model=DedupRunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_dedup_run(
    payload: DedupRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> DedupRunAccepted:
    """Queue deduplication over the union of the requested query versions."""

    run = await create_dedup_run(db, payload.query_version_ids)
    background_tasks.add_task(run_dedup_task, run.id)
    return DedupRunAccepted(run_id=run.id, query_version_ids=run.params["query_version_ids"])


@router.get("/groups", response_model=DedupGroupPage)
async def list_dedup_groups(
    query_version_id: int,
    only_multi: bool = True,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> DedupGroupPage:
    """List the latest materialized groups for a query version's dedup scope."""

    run = await _latest_scope_run(db, query_version_id)
    group_ids = list(
        (
            await db.scalars(
                select(DedupGroup.id)
                .join(GroupMember, GroupMember.group_id == DedupGroup.id)
                .where(DedupGroup.run_id == run.id)
                .group_by(DedupGroup.id)
                .having(func.count(GroupMember.record_id) > (1 if only_multi else 0))
                .order_by(DedupGroup.id)
            )
        ).all()
    )
    selected_ids = group_ids[offset : offset + limit]
    return DedupGroupPage(
        items=await _serialize_groups(db, selected_ids),
        limit=limit,
        offset=offset,
        total=len(group_ids),
    )


@router.get("/pairs", response_model=DedupPairPage)
async def list_dedup_pairs(
    status: str = Query(pattern="^(uncertain|duplicate|distinct)$"),
    query_version_id: int = Query(),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> DedupPairPage:
    """List review pairs with both records' identifying fields."""

    run = await _latest_scope_run(db, query_version_id)
    scope = [int(value) for value in run.params["query_version_ids"]]
    record_ids = list(
        (
            await db.scalars(
                select(QueryRecord.record_id)
                .where(QueryRecord.query_version_id.in_(scope))
                .distinct()
            )
        ).all()
    )
    pairs = [] if not record_ids else list(
        (
            await db.scalars(
                select(DedupPair)
                .where(DedupPair.record_a_id.in_(record_ids))
                .where(DedupPair.record_b_id.in_(record_ids))
                .order_by(DedupPair.record_a_id, DedupPair.record_b_id)
            )
        ).all()
    )
    pairs = [pair for pair in pairs if (pair.human_verdict or pair.machine_verdict) == status]
    selected = pairs[offset : offset + limit]
    records = await _records_by_id(db, {value for pair in selected for value in (pair.record_a_id, pair.record_b_id)})
    return DedupPairPage(
        items=[_serialize_pair(pair, records) for pair in selected],
        limit=limit,
        offset=offset,
        total=len(pairs),
    )


@router.post("/pairs/{record_a_id}/{record_b_id}/verdict", response_model=DedupVerdictResponse)
async def set_pair_verdict(
    record_a_id: int,
    record_b_id: int,
    payload: DedupVerdictRequest,
    db: AsyncSession = Depends(get_db),
) -> DedupVerdictResponse:
    """Apply/clear a human verdict and return groups touched by the rebuild."""

    run_id, group_ids = await apply_human_verdict(
        db, record_a_id, record_b_id, payload.verdict, payload.reviewer
    )
    return DedupVerdictResponse(run_id=run_id, groups=await _serialize_groups(db, group_ids))


async def _latest_scope_run(db, query_version_id: int) -> RunLog:
    runs = list(
        (
            await db.scalars(
                select(RunLog)
                .where(RunLog.step == "dedup", RunLog.status == "done")
                .order_by(RunLog.id.desc())
            )
        ).all()
    )
    run = next(
        (item for item in runs if query_version_id in (item.params or {}).get("query_version_ids", [])),
        None,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="No completed dedup run for query version")
    return run


async def _serialize_groups(db, group_ids: list[int]) -> list[DedupGroupRead]:
    if not group_ids:
        return []
    groups = list(
        (
            await db.scalars(select(DedupGroup).where(DedupGroup.id.in_(group_ids)).order_by(DedupGroup.id))
        ).all()
    )
    memberships = list(
        (
            await db.scalars(
                select(GroupMember)
                .where(GroupMember.group_id.in_(group_ids))
                .order_by(GroupMember.group_id, GroupMember.record_id)
            )
        ).all()
    )
    records = await _records_by_id(db, {membership.record_id for membership in memberships})
    by_group: dict[int, list[GroupMember]] = {}
    for membership in memberships:
        by_group.setdefault(membership.group_id, []).append(membership)

    output = []
    for group in groups:
        members = by_group.get(group.id, [])
        member_ids = {member.record_id for member in members}
        evidence = [] if len(member_ids) < 2 else list(
            (
                await db.scalars(
                    select(DedupPair)
                    .where(DedupPair.record_a_id.in_(member_ids))
                    .where(DedupPair.record_b_id.in_(member_ids))
                    .order_by(DedupPair.record_a_id, DedupPair.record_b_id)
                )
            ).all()
        )
        output.append(DedupGroupRead(
            id=group.id,
            run_id=group.run_id,
            representative_record_id=group.representative_record_id,
            members=[
                DedupGroupMemberRead(
                    record=DedupRecordSummary.model_validate(records[member.record_id]),
                    is_representative=member.is_representative,
                )
                for member in members
            ],
            pair_evidence=[
                DedupPairEvidence(
                    record_a_id=pair.record_a_id,
                    record_b_id=pair.record_b_id,
                    method=pair.method,
                    score=pair.score,
                    effective_verdict=pair.human_verdict or pair.machine_verdict,
                )
                for pair in evidence
            ],
        ))
    return output


async def _records_by_id(db, record_ids: set[int]) -> dict[int, Record]:
    if not record_ids:
        return {}
    records = list((await db.scalars(select(Record).where(Record.id.in_(record_ids)))).all())
    return {record.id: record for record in records}


def _serialize_pair(pair: DedupPair, records: dict[int, Record]) -> DedupPairRead:
    return DedupPairRead(
        record_a=DedupRecordSummary.model_validate(records[pair.record_a_id]),
        record_b=DedupRecordSummary.model_validate(records[pair.record_b_id]),
        method=pair.method,
        score=pair.score,
        machine_verdict=pair.machine_verdict,
        human_verdict=pair.human_verdict,
        effective_verdict=pair.human_verdict or pair.machine_verdict,
        human_by=pair.human_by,
        human_at=pair.human_at,
    )
