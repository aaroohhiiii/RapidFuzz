"""Persistence and background-job orchestration for deduplication."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import AuditLog, DedupGroup, DedupPair, GroupMember, QueryRecord, QueryVersion, Record, RunLog
from app.services.dedup_algorithm import (
    DedupRecord,
    PairResult,
    choose_representative,
    connected_components,
    find_candidate_pairs,
)


async def create_dedup_run(
    db, query_version_ids: list[int], new_record_ids: list[int] | None = None
) -> RunLog:
    """Validate a union scope and persist a queued dedup run."""

    scope = sorted(set(query_version_ids))
    if not scope:
        raise HTTPException(status_code=422, detail="query_version_ids must not be empty")
    existing_ids = set((await db.scalars(select(QueryVersion.id).where(QueryVersion.id.in_(scope)))).all())
    missing = [version_id for version_id in scope if version_id not in existing_ids]
    if missing:
        raise HTTPException(status_code=404, detail={"missing_query_version_ids": missing})

    run = RunLog(
        query_version_id=scope[0],
        step="dedup",
        rule_version=settings.DEDUP_RULE_VERSION,
        params={**_run_params(scope), "new_record_ids": sorted(set(new_record_ids or []))},
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def run_dedup_task(run_id: int) -> None:
    """Execute a dedup run in its own database session."""

    async with AsyncSessionLocal() as db:
        try:
            await execute_dedup_run(db, run_id)
        except Exception as exc:
            await db.rollback()
            run = await db.get(RunLog, run_id)
            if run is not None:
                run.status = "failed"
                run.stats = {"error": str(exc)}
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()


async def execute_dedup_run(db, run_id: int) -> dict[str, int]:
    """Score pairs and materialize deterministic groups for one persisted run."""

    run = await db.get(RunLog, run_id)
    if run is None or run.step != "dedup":
        raise ValueError(f"Dedup run {run_id} not found")
    if run.status == "done" and run.stats is not None:
        return run.stats

    scope = _scope_from_run(run)
    records = await _load_scope_records(db, scope)
    incremental_ids = set((run.params or {}).get("new_record_ids") or [])
    pair_results = find_candidate_pairs(records, incremental_ids or None)
    for result in pair_results:
        await db.execute(_pair_upsert(db, result, run.id))
    await db.flush()

    components = await _materialize_groups(db, run, records)
    stats = {
        "records": len(records),
        "pairs_exact": sum(result.method in {"doi", "pmid"} for result in pair_results),
        "pairs_fuzzy_dup": sum(
            result.method == "fuzzy" and result.machine_verdict == "duplicate" for result in pair_results
        ),
        "pairs_uncertain": sum(result.machine_verdict == "uncertain" for result in pair_results),
        "groups": len(components),
        "hidden": sum(len(component) - 1 for component in components),
    }
    run.stats = stats
    run.status = "done"
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return stats


async def apply_human_verdict(
    db,
    record_a_id: int,
    record_b_id: int,
    verdict: str | None,
    reviewer: str,
) -> tuple[int, list[int]]:
    """Set/clear a pair override, audit it, and atomically rebuild its scope."""

    left_id, right_id = sorted((record_a_id, record_b_id))
    pair = await db.get(DedupPair, (left_id, right_id))
    if pair is None:
        raise HTTPException(status_code=404, detail="Dedup pair not found")
    source_run = await db.get(RunLog, pair.run_id) if pair.run_id is not None else None
    if source_run is None:
        raise HTTPException(status_code=409, detail="Dedup pair has no source run")
    scope = _scope_from_run(source_run)

    before = _human_state(pair)
    pair.human_verdict = verdict
    pair.human_by = reviewer if verdict is not None else None
    pair.human_at = datetime.now(timezone.utc) if verdict is not None else None
    after = _human_state(pair)
    db.add(
        AuditLog(
            actor=reviewer,
            action="dedup_override",
            entity_type="dedup_pair",
            entity_id=f"{left_id}:{right_id}",
            before_value=before,
            after_value=after,
            query_version_id=scope[0],
        )
    )

    params = _run_params(scope)
    params["rebuild_reason"] = "human_verdict"
    rebuild_run = RunLog(
        query_version_id=scope[0],
        step="dedup",
        rule_version=settings.DEDUP_RULE_VERSION,
        params=params,
        status="running",
    )
    db.add(rebuild_run)
    await db.flush()
    records = await _load_scope_records(db, scope)
    components = await _materialize_groups(db, rebuild_run, records)
    pairs = await _scope_pairs(db, [record.id for record in records])
    rebuild_run.stats = _stats_from_pairs(records, pairs, components)
    rebuild_run.status = "done"
    rebuild_run.finished_at = datetime.now(timezone.utc)
    await db.flush()

    touched_group_ids = list(
        (
            await db.scalars(
                select(GroupMember.group_id)
                .join(DedupGroup, DedupGroup.id == GroupMember.group_id)
                .where(DedupGroup.run_id == rebuild_run.id)
                .where(GroupMember.record_id.in_([left_id, right_id]))
                .distinct()
                .order_by(GroupMember.group_id)
            )
        ).all()
    )
    await db.commit()
    return rebuild_run.id, touched_group_ids


async def _load_scope_records(db, query_version_ids: list[int]) -> list[DedupRecord]:
    result = await db.execute(
        select(Record)
        .join(QueryRecord, QueryRecord.record_id == Record.id)
        .where(QueryRecord.query_version_id.in_(query_version_ids))
        .distinct()
        .order_by(Record.id)
    )
    return [_to_dedup_record(record) for record in result.scalars().all()]


async def _materialize_groups(db, run: RunLog, records: list[DedupRecord]) -> list[list[int]]:
    record_ids = [record.id for record in records]
    pairs = await _scope_pairs(db, record_ids)
    duplicate_edges = [
        (pair.record_a_id, pair.record_b_id)
        for pair in pairs
        if (pair.human_verdict or pair.machine_verdict) == "duplicate"
    ]
    components = connected_components(record_ids, duplicate_edges)
    by_id = {record.id: record for record in records}
    for component in components:
        representative_id = choose_representative([by_id[record_id] for record_id in component])
        group = DedupGroup(representative_record_id=representative_id, run_id=run.id)
        db.add(group)
        await db.flush()
        db.add_all(
            GroupMember(
                group_id=group.id,
                record_id=record_id,
                is_representative=record_id == representative_id,
            )
            for record_id in component
        )
    await db.flush()
    return components


async def _scope_pairs(db, record_ids: list[int]) -> list[DedupPair]:
    if not record_ids:
        return []
    return list(
        (
            await db.scalars(
                select(DedupPair)
                .where(DedupPair.record_a_id.in_(record_ids))
                .where(DedupPair.record_b_id.in_(record_ids))
                .order_by(DedupPair.record_a_id, DedupPair.record_b_id)
            )
        ).all()
    )


def _pair_upsert(db, result: PairResult, run_id: int):
    insert_fn = sqlite_insert if db.bind.dialect.name == "sqlite" else postgres_insert
    statement = insert_fn(DedupPair).values(
        record_a_id=result.record_a_id,
        record_b_id=result.record_b_id,
        score=result.score,
        method=result.method,
        machine_verdict=result.machine_verdict,
        run_id=run_id,
    )
    return statement.on_conflict_do_update(
        index_elements=["record_a_id", "record_b_id"],
        set_={
            "score": statement.excluded.score,
            "method": statement.excluded.method,
            "machine_verdict": statement.excluded.machine_verdict,
            "run_id": statement.excluded.run_id,
        },
    )


def _to_dedup_record(record: Record) -> DedupRecord:
    return DedupRecord(
        id=record.id,
        source=record.source,
        doi=record.doi,
        pmid=record.pmid,
        title_clean=record.title_clean,
        abstract=record.abstract,
        first_author=record.first_author,
        year=record.year,
        journal=record.journal,
        pages=record.pages,
        pub_types=tuple(record.pub_types or ()),
    )


def _scope_from_run(run: RunLog) -> list[int]:
    scope = (run.params or {}).get("query_version_ids")
    if not isinstance(scope, list) or not scope:
        raise ValueError(f"Dedup run {run.id} has no query_version_ids scope")
    return sorted({int(version_id) for version_id in scope})


def _run_params(scope: list[int]) -> dict[str, Any]:
    return {
        "query_version_ids": scope,
        "dup_high": settings.DUP_HIGH,
        "dup_low": settings.DUP_LOW,
        "weights": {
            "title": settings.DEDUP_TITLE_WEIGHT,
            "year": settings.DEDUP_YEAR_WEIGHT,
            "first_author": settings.DEDUP_AUTHOR_WEIGHT,
            "journal": settings.DEDUP_JOURNAL_WEIGHT,
            "pages": settings.DEDUP_PAGES_WEIGHT,
        },
        "companion_guard": {
            "near_title": settings.DEDUP_NEAR_TITLE,
            "abstract_low": settings.DEDUP_ABSTRACT_LOW,
            "abstract_min_chars": settings.DEDUP_ABSTRACT_MIN_CHARS,
            "conference_types": list(settings.DEDUP_CONFERENCE_TYPES),
        },
    }


def _human_state(pair: DedupPair) -> dict[str, str | None]:
    return {
        "human_verdict": pair.human_verdict,
        "human_by": pair.human_by,
        "human_at": pair.human_at.isoformat() if pair.human_at else None,
    }


def _stats_from_pairs(
    records: list[DedupRecord], pairs: list[DedupPair], components: list[list[int]]
) -> dict[str, int]:
    return {
        "records": len(records),
        "pairs_exact": sum(pair.method in {"doi", "pmid"} for pair in pairs),
        "pairs_fuzzy_dup": sum(
            pair.method == "fuzzy" and pair.machine_verdict == "duplicate" for pair in pairs
        ),
        "pairs_uncertain": sum(pair.machine_verdict == "uncertain" for pair in pairs),
        "groups": len(components),
        "hidden": sum(len(component) - 1 for component in components),
    }
