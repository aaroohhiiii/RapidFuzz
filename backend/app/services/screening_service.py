"""Incremental screening runs and combined machine-decision persistence."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import AuditLog, Criteria, MachineDecision, MachineScreening, QueryRecord, QueryVersion, Record, RunLog
from app.services.screening_rules import (
    DecisionInput,
    RULE_CODES,
    ScreeningRecord,
    combine_decision,
    evaluate_rule,
    rule_versions,
)


async def create_screening_run(db, query_version_id: int) -> RunLog:
    """Validate a query version and persist a queued screening run."""

    if await db.get(QueryVersion, query_version_id) is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    run = RunLog(
        query_version_id=query_version_id,
        step="screen",
        rule_version=settings.SCREEN_RUN_VERSION,
        params={
            "query_version_id": query_version_id,
            "rule_versions": rule_versions(),
            "decision_version": settings.SCREEN_DECISION_VERSION,
        },
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def run_screening_task(run_id: int) -> None:
    """Execute a screening run in a separate database session."""

    async with AsyncSessionLocal() as db:
        try:
            await execute_screening_run(db, run_id)
        except Exception as exc:
            await db.rollback()
            run = await db.get(RunLog, run_id)
            if run is not None:
                run.status = "failed"
                run.stats = {"error": str(exc)}
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()


async def execute_screening_run(db, run_id: int) -> dict:
    """Evaluate missing rule versions and upsert combined decisions."""

    run = await db.get(RunLog, run_id)
    if run is None or run.step != "screen" or run.query_version_id is None:
        raise ValueError(f"Screening run {run_id} not found")
    if run.status == "done" and run.stats is not None:
        return run.stats
    records = await _load_records(db, run.query_version_id)
    criteria = await _load_criteria(db)
    versions = rule_versions()
    existing = await _existing_keys(db, records, criteria)

    screened_new = 0
    records_screened_new: set[int] = set()
    skipped = 0
    for record in records:
        screening_record = _to_screening_record(record)
        for criterion in criteria:
            version = versions[criterion.code]
            key = (record.id, criterion.id, version)
            if key in existing:
                skipped += 1
                continue
            result = evaluate_rule(criterion.code, screening_record)
            db.add(MachineScreening(
                record_id=record.id,
                criterion_id=criterion.id,
                result=result.result,
                evidence=result.evidence,
                rule_version=version,
                run_id=run.id,
            ))
            screened_new += 1
            records_screened_new.add(record.id)
    await db.flush()
    decisions = await _recompute_decisions(db, run, records, criteria)
    stats = _build_stats(len(records), screened_new, skipped, decisions)
    stats["records_screened_new"] = len(records_screened_new)
    run.stats = stats
    run.status = "done"
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return stats


async def recompute_query_decisions(db, run: RunLog, query_version_id: int) -> dict:
    """Recombine stored criterion results without evaluating any rule."""

    records = await _load_records(db, query_version_id)
    criteria = await _load_criteria(db)
    decisions = await _recompute_decisions(db, run, records, criteria)
    stats = _build_stats(len(records), 0, 0, decisions)
    run.stats = stats
    run.status = "done"
    run.finished_at = datetime.now(timezone.utc)
    return stats


async def toggle_criterion(
    db, criterion_id: int, active: bool, reviewer: str
) -> tuple[Criteria, list[int], dict]:
    """Audit an active-state change and recompute all previously screened scopes."""

    criterion = await db.get(Criteria, criterion_id)
    if criterion is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    before = {"active": criterion.active}
    criterion.active = active
    db.add(AuditLog(
        actor=reviewer,
        action="criterion_toggle",
        entity_type="criterion",
        entity_id=str(criterion.id),
        before_value=before,
        after_value={"active": active},
        query_version_id=None,
    ))
    query_version_ids = list(
        (
            await db.scalars(
                select(RunLog.query_version_id)
                .where(RunLog.step == "screen", RunLog.status == "done")
                .where(RunLog.query_version_id.is_not(None))
                .distinct()
                .order_by(RunLog.query_version_id)
            )
        ).all()
    )
    recomputed_runs = []
    for query_version_id in query_version_ids:
        run = RunLog(
            query_version_id=query_version_id,
            step="screen",
            rule_version=settings.SCREEN_RUN_VERSION,
            params={
                "query_version_id": query_version_id,
                "recompute_only": True,
                "trigger": {"criterion_id": criterion.id, "active": active},
                "rule_versions": rule_versions(),
                "decision_version": settings.SCREEN_DECISION_VERSION,
            },
            status="running",
        )
        db.add(run)
        await db.flush()
        await recompute_query_decisions(db, run, query_version_id)
        recomputed_runs.append(run.id)
    await db.flush()
    counts = await screening_summary_for_all_decisions(db)
    await db.commit()
    await db.refresh(criterion)
    return criterion, recomputed_runs, counts


async def screening_summary(db, query_version_id: int) -> dict:
    """Return current decision and exclusion-reason counts for one query version."""

    if await db.get(QueryVersion, query_version_id) is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    record_ids = list(
        (
            await db.scalars(
                select(QueryRecord.record_id).where(QueryRecord.query_version_id == query_version_id)
            )
        ).all()
    )
    return await _summary_for_record_ids(db, record_ids)


async def screening_summary_for_all_decisions(db) -> dict:
    """Return current counts across all records with a combined decision."""

    record_ids = list(
        (
            await db.scalars(
                select(MachineDecision.record_id)
                .where(MachineDecision.rule_version == settings.SCREEN_DECISION_VERSION)
                .distinct()
            )
        ).all()
    )
    return await _summary_for_record_ids(db, record_ids)


async def _recompute_decisions(db, run: RunLog, records: list[Record], criteria: list[Criteria]):
    versions = rule_versions()
    criterion_by_id = {criterion.id: criterion for criterion in criteria}
    record_ids = [record.id for record in records]
    rows = [] if not record_ids else list(
        (
            await db.scalars(
                select(MachineScreening)
                .where(MachineScreening.record_id.in_(record_ids))
                .where(MachineScreening.criterion_id.in_(criterion_by_id))
            )
        ).all()
    )
    current = {
        (row.record_id, criterion_by_id[row.criterion_id].code): row
        for row in rows
        if row.rule_version == versions[criterion_by_id[row.criterion_id].code]
    }
    decisions: list[tuple[str, list[str]]] = []
    for record in records:
        inputs = []
        for criterion in criteria:
            row = current.get((record.id, criterion.code))
            inputs.append(DecisionInput(
                code=criterion.code,
                kind=criterion.kind,
                active=criterion.active,
                result=row.result if row else "unknown",
                evidence=row.evidence if row else "configured rule version has not run",
            ))
        decision, reasons = combine_decision(inputs, has_abstract=bool(record.abstract))
        existing_decision = await db.get(
            MachineDecision, (record.id, settings.SCREEN_DECISION_VERSION)
        )
        await db.execute(_decision_upsert(db, record.id, decision, reasons, run.id))
        if existing_decision is not None:
            await db.refresh(existing_decision)
        decisions.append((decision, reasons))
    await db.flush()
    return decisions


async def _load_records(db, query_version_id: int) -> list[Record]:
    return list(
        (
            await db.scalars(
                select(Record)
                .join(QueryRecord, QueryRecord.record_id == Record.id)
                .where(QueryRecord.query_version_id == query_version_id)
                .order_by(Record.id)
            )
        ).all()
    )


async def _load_criteria(db) -> list[Criteria]:
    criteria = list((await db.scalars(select(Criteria).order_by(Criteria.id))).all())
    codes = {criterion.code for criterion in criteria}
    missing = sorted(set(RULE_CODES) - codes)
    if missing:
        raise HTTPException(status_code=409, detail={"seed_missing_criteria": missing})
    return [criterion for criterion in criteria if criterion.code in RULE_CODES]


async def _existing_keys(db, records: list[Record], criteria: list[Criteria]) -> set[tuple[int, int, str]]:
    record_ids = [record.id for record in records]
    if not record_ids:
        return set()
    rows = (
        await db.execute(
            select(
                MachineScreening.record_id,
                MachineScreening.criterion_id,
                MachineScreening.rule_version,
            )
            .where(MachineScreening.record_id.in_(record_ids))
            .where(MachineScreening.criterion_id.in_([criterion.id for criterion in criteria]))
        )
    ).all()
    return set(rows)


def _decision_upsert(db, record_id: int, decision: str, reasons: list[str], run_id: int):
    insert_fn = sqlite_insert if db.bind.dialect.name == "sqlite" else postgres_insert
    statement = insert_fn(MachineDecision).values(
        record_id=record_id,
        rule_version=settings.SCREEN_DECISION_VERSION,
        decision=decision,
        reasons=reasons,
        run_id=run_id,
    )
    return statement.on_conflict_do_update(
        index_elements=["record_id", "rule_version"],
        set_={
            "decision": statement.excluded.decision,
            "reasons": statement.excluded.reasons,
            "run_id": statement.excluded.run_id,
        },
    )


def _to_screening_record(record: Record) -> ScreeningRecord:
    return ScreeningRecord(
        id=record.id,
        title=record.title,
        abstract=record.abstract,
        language=record.language,
        pub_types=tuple(record.pub_types or ()),
        mesh_terms=tuple(record.mesh_terms or ()),
    )


def _build_stats(records: int, screened_new: int, skipped: int, decisions) -> dict:
    decision_counts = Counter(decision for decision, _ in decisions)
    reason_counts = Counter(
        reason for decision, reasons in decisions if decision == "exclude" for reason in reasons
    )
    return {
        "records": records,
        "screened_new": screened_new,
        "skipped": skipped,
        "include": decision_counts["include"],
        "exclude": decision_counts["exclude"],
        "maybe": decision_counts["maybe"],
        "by_reason": dict(sorted(reason_counts.items())),
    }


async def _summary_for_record_ids(db, record_ids: list[int]) -> dict:
    decisions = [] if not record_ids else list(
        (
            await db.scalars(
                select(MachineDecision)
                .where(MachineDecision.record_id.in_(record_ids))
                .where(MachineDecision.rule_version == settings.SCREEN_DECISION_VERSION)
            )
        ).all()
    )
    decision_counts = Counter(decision.decision for decision in decisions)
    reason_counts = Counter(
        reason
        for decision in decisions
        if decision.decision == "exclude"
        for reason in (decision.reasons or [])
    )
    return {
        "records": len(record_ids),
        "include": decision_counts["include"],
        "exclude": decision_counts["exclude"],
        "maybe": decision_counts["maybe"],
        "by_reason": dict(sorted(reason_counts.items())),
    }
