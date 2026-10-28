"""Human review, effective status, history, audit, conflicts, and CSV export."""

from __future__ import annotations

from collections import Counter
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import (
    AuditLog,
    DedupGroup,
    GroupMember,
    MachineDecision,
    QueryRecord,
    QueryVersion,
    Record,
)
from app.schemas import (
    AuditPage,
    AuditRead,
    ConflictMemberRead,
    ConflictRead,
    DedupRecordSummary,
    EffectivePicoSpanRead,
    EffectiveStatusRead,
    GroupResolveResponse,
    HistoryEventRead,
    HumanDecisionRequest,
    PicoHumanRequest,
    RecordHistoryRead,
    RecordPicoRead,
    ReviewRecordPage,
    ReviewRecordRead,
    UndoDecisionRequest,
)
from app.services.pico_service import get_effective_spans
from app.services.review_service import (
    add_human_decision,
    add_pico_human_action,
    effective_status,
    latest_human_decisions,
    record_history,
    resolve_group,
    review_groups_for_query,
    undo_human_decision,
)


router = APIRouter(tags=["review"])


@router.get("/review/records", response_model=ReviewRecordPage)
async def list_review_records(
    query_version_id: int,
    status: str | None = Query(default=None, pattern="^(include|exclude|maybe|conflict)$"),
    decided_by: str | None = Query(default=None, pattern="^(machine|human)$"),
    reason: str | None = None,
    source: str | None = None,
    q: str | None = None,
    sort: str = "record_id",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_hidden: bool = False,
    db: AsyncSession = Depends(get_db),
) -> ReviewRecordPage:
    """List review rows using shared effective decisions and PICO spans."""

    if await db.get(QueryVersion, query_version_id) is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    groups = await review_groups_for_query(db, query_version_id)
    rows: list[ReviewRecordRead] = []
    for group in groups:
        display_ids = group.member_ids if include_hidden else (group.representative_record_id,)
        effective = await effective_status(db, group.representative_record_id, group.group_id)
        representative = await db.get(Record, group.representative_record_id)
        machine = await db.get(
            MachineDecision,
            (group.representative_record_id, settings.SCREEN_DECISION_VERSION),
        )
        pico = await get_effective_spans(db, representative)
        for record_id in display_ids:
            record = await db.get(Record, record_id)
            rows.append(ReviewRecordRead(
                record=DedupRecordSummary.model_validate(record),
                effective=EffectiveStatusRead(**effective.__dict__),
                machine_decision=machine.decision if machine else None,
                machine_reasons=list(machine.reasons or []) if machine else [],
                group_size=len(group.member_ids),
                hidden=record_id != group.representative_record_id,
                pico_spans=[EffectivePicoSpanRead(**span.__dict__) for span in pico],
            ))

    rows = _review_filters(rows, decided_by, reason, source, q)
    counts = Counter(row.effective.status for row in rows)
    if status:
        rows = [row for row in rows if row.effective.status == status]
    rows = _sort_review_rows(rows, sort)
    return ReviewRecordPage(
        items=rows[offset : offset + limit],
        counts={value: counts[value] for value in ("include", "exclude", "maybe", "conflict")},
        limit=limit,
        offset=offset,
        total=len(rows),
    )


@router.post("/records/{record_id}/decision", response_model=EffectiveStatusRead)
async def post_record_decision(
    record_id: int,
    payload: HumanDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> EffectiveStatusRead:
    result = await add_human_decision(
        db, record_id, payload.decision, payload.reason, payload.note, payload.reviewer
    )
    return EffectiveStatusRead(**result.__dict__)


@router.post("/records/{record_id}/decision/undo", response_model=EffectiveStatusRead)
async def undo_record_decision(
    record_id: int,
    payload: UndoDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> EffectiveStatusRead:
    return EffectiveStatusRead(**(await undo_human_decision(db, record_id, payload.reviewer)).__dict__)


@router.post("/records/{record_id}/pico", response_model=RecordPicoRead)
async def post_record_pico(
    record_id: int,
    payload: PicoHumanRequest,
    db: AsyncSession = Depends(get_db),
) -> RecordPicoRead:
    await add_pico_human_action(
        db,
        record_id,
        payload.action,
        payload.label,
        payload.start_char,
        payload.end_char,
        payload.machine_span_id,
        payload.reviewer,
    )
    record = await db.get(Record, record_id)
    spans = await get_effective_spans(db, record)
    return RecordPicoRead(
        record_id=record_id,
        spans=[EffectivePicoSpanRead(**span.__dict__) for span in spans],
    )


@router.get("/records/{record_id}/history", response_model=RecordHistoryRead)
async def get_record_history(record_id: int, db: AsyncSession = Depends(get_db)) -> RecordHistoryRead:
    events = await record_history(db, record_id)
    return RecordHistoryRead(
        record_id=record_id,
        events=[HistoryEventRead(**event) for event in events],
    )


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AuditPage:
    statement = select(AuditLog)
    if entity_type:
        statement = statement.where(AuditLog.entity_type == entity_type)
    if entity_id:
        statement = statement.where(AuditLog.entity_id == entity_id)
    if actor:
        statement = statement.where(AuditLog.actor == actor)
    rows = list((await db.scalars(statement.order_by(AuditLog.ts.desc(), AuditLog.id.desc()))).all())
    return AuditPage(
        items=[AuditRead.model_validate(row) for row in rows[offset : offset + limit]],
        limit=limit,
        offset=offset,
        total=len(rows),
    )


@router.get("/conflicts", response_model=list[ConflictRead])
async def list_conflicts(query_version_id: int, db: AsyncSession = Depends(get_db)) -> list[ConflictRead]:
    output = []
    for group in await review_groups_for_query(db, query_version_id):
        if group.group_id is None:
            continue
        effective = await effective_status(db, group.representative_record_id, group.group_id)
        if effective.status != "conflict":
            continue
        latest = await latest_human_decisions(db, list(group.member_ids))
        records = {
            record.id: record
            for record in (
                await db.scalars(select(Record).where(Record.id.in_(group.member_ids)))
            ).all()
        }
        output.append(ConflictRead(
            group_id=group.group_id,
            representative_record_id=group.representative_record_id,
            members=[
                ConflictMemberRead(
                    record=DedupRecordSummary.model_validate(records[record_id]),
                    latest_decision=latest[record_id].decision if record_id in latest else None,
                    reviewer=latest[record_id].reviewer if record_id in latest else None,
                    decided_at=latest[record_id].created_at if record_id in latest else None,
                )
                for record_id in group.member_ids
            ],
        ))
    return output


@router.post("/groups/{group_id}/resolve", response_model=GroupResolveResponse)
async def resolve_group_conflict(
    group_id: int,
    payload: HumanDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> GroupResolveResponse:
    result = await resolve_group(
        db, group_id, payload.decision, payload.reason, payload.note, payload.reviewer
    )
    return GroupResolveResponse(group_id=group_id, effective=EffectiveStatusRead(**result.__dict__))


@router.get("/export/included.csv")
async def export_included_csv(
    query_version_id: int, db: AsyncSession = Depends(get_db)
) -> Response:
    """Export one UTF-8-BOM CSV row per effectively included group."""

    columns = [
        "record_id", "group_id", "source", "pmid", "doi", "title", "authors", "year",
        "journal", "abstract", "decision", "decided_by", "reason", "note", "population",
        "intervention", "comparator", "outcome", "found_by_query_versions", "other_source_ids",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    for group in await review_groups_for_query(db, query_version_id):
        effective = await effective_status(db, group.representative_record_id, group.group_id)
        if effective.status != "include":
            continue
        record = await db.get(Record, group.representative_record_id)
        spans = await get_effective_spans(db, record)
        by_label = {
            label: " | ".join(span.span_text for span in spans if span.label == label)
            for label in ("P", "I", "C", "O")
        }
        found_versions = sorted(set(
            (
                await db.scalars(
                    select(QueryRecord.query_version_id)
                    .where(QueryRecord.record_id.in_(group.member_ids))
                )
            ).all()
        ))
        other_records = list(
            (
                await db.scalars(
                    select(Record)
                    .where(Record.id.in_(group.member_ids))
                    .where(Record.id != group.representative_record_id)
                    .order_by(Record.id)
                )
            ).all()
        ) if len(group.member_ids) > 1 else []
        writer.writerow({
            "record_id": record.id,
            "group_id": group.group_id or "",
            "source": record.source,
            "pmid": record.pmid or "",
            "doi": record.doi or "",
            "title": record.title or "",
            "authors": " | ".join(record.authors or []),
            "year": record.year or "",
            "journal": record.journal or "",
            "abstract": record.abstract or "",
            "decision": effective.status,
            "decided_by": effective.decided_by,
            "reason": " | ".join(effective.reasons),
            "note": effective.note or "",
            "population": by_label["P"],
            "intervention": by_label["I"],
            "comparator": by_label["C"],
            "outcome": by_label["O"],
            "found_by_query_versions": " | ".join(str(value) for value in found_versions),
            "other_source_ids": " | ".join(f"{item.source}:{item.source_id}" for item in other_records),
        })
    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="included-{query_version_id}.csv"'},
    )


def _review_filters(rows, decided_by, reason, source, q):
    if decided_by:
        rows = [row for row in rows if row.effective.decided_by == decided_by]
    if reason:
        needle = reason.casefold()
        rows = [row for row in rows if any(needle in value.casefold() for value in row.effective.reasons)]
    if source:
        rows = [row for row in rows if row.record.source == source]
    if q:
        needle = q.casefold()
        rows = [
            row for row in rows
            if needle in " ".join(
                filter(None, [row.record.title, row.record.abstract, row.record.doi, row.record.pmid])
            ).casefold()
        ]
    return rows


def _sort_review_rows(rows, sort):
    descending = sort.startswith("-")
    key = sort.removeprefix("-")
    key_functions = {
        "record_id": lambda row: row.record.id,
        "title": lambda row: (row.record.title or "").casefold(),
        "year": lambda row: row.record.year or 0,
        "source": lambda row: row.record.source,
        "status": lambda row: row.effective.status,
    }
    if key not in key_functions:
        raise HTTPException(status_code=422, detail="Unsupported sort")
    return sorted(rows, key=key_functions[key], reverse=descending)
