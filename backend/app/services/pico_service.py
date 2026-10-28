"""Incremental PICO tagging jobs with abstract-hash provenance."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import MachineDecision, PicoHuman, PicoMachine, QueryRecord, QueryVersion, Record, RunLog
from app.services.pico_effective import EffectivePicoSpan, merge_effective_spans
from app.services.pico_rules import PicoSpan, tag_abstract


async def create_pico_run(db, query_version_id: int) -> RunLog:
    """Validate a query version and persist a queued PICO run."""

    if await db.get(QueryVersion, query_version_id) is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    run = RunLog(
        query_version_id=query_version_id,
        step="pico",
        rule_version=settings.PICO_RULE_VERSION,
        params={"query_version_id": query_version_id},
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def run_pico_task(run_id: int) -> None:
    """Execute a PICO run in a separate database session."""

    async with AsyncSessionLocal() as db:
        try:
            await execute_pico_run(db, run_id)
        except Exception as exc:
            await db.rollback()
            run = await db.get(RunLog, run_id)
            if run is not None:
                run.status = "failed"
                run.stats = {"error": str(exc)}
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()


async def execute_pico_run(db, run_id: int) -> dict:
    """Tag eligible abstracts, skipping unchanged hashes for this rule version."""

    run = await db.get(RunLog, run_id)
    if run is None or run.step != "pico" or run.query_version_id is None:
        raise ValueError(f"PICO run {run_id} not found")
    if run.status == "done" and run.stats is not None:
        return run.stats
    records = await _eligible_records(db, run.query_version_id)
    prior_runs = await _completed_rule_runs(db)
    current_spans: dict[int, list[PicoSpan | PicoMachine]] = {}
    abstract_hashes: dict[str, str] = {}
    processed_new = 0
    skipped_unchanged = 0

    for record in records:
        abstract = record.abstract or ""
        digest = abstract_hash(abstract)
        prior_run_id = _matching_run_id(prior_runs, record.id, digest)
        if prior_run_id is not None:
            current_spans[record.id] = list(
                (
                    await db.scalars(
                        select(PicoMachine)
                        .where(PicoMachine.record_id == record.id)
                        .where(PicoMachine.rule_version == settings.PICO_RULE_VERSION)
                        .where(PicoMachine.run_id == prior_run_id)
                        .order_by(PicoMachine.start_char, PicoMachine.end_char, PicoMachine.id)
                    )
                ).all()
            )
            skipped_unchanged += 1
            continue
        spans = tag_abstract(abstract)
        for span in spans:
            if abstract[span.start_char : span.end_char] != span.span_text:
                raise ValueError(f"PICO offset invariant violated for record {record.id}")
            db.add(PicoMachine(
                record_id=record.id,
                label=span.label,
                start_char=span.start_char,
                end_char=span.end_char,
                span_text=span.span_text,
                method="rules",
                confidence=span.confidence,
                rule_version=settings.PICO_RULE_VERSION,
                run_id=run.id,
            ))
        current_spans[record.id] = spans
        abstract_hashes[str(record.id)] = digest
        processed_new += 1

    label_counts = Counter(
        span.label for spans in current_spans.values() for span in spans
    )
    label_sets = [{span.label for span in spans} for spans in current_spans.values()]
    stats = {
        "records_tagged": len(records),
        "spans_by_label": {label: label_counts[label] for label in ("P", "I", "C", "O")},
        "records_with_all_four": sum(labels == {"P", "I", "C", "O"} for labels in label_sets),
        "records_with_none": sum(not labels for labels in label_sets),
        "processed_new": processed_new,
        "skipped_unchanged": skipped_unchanged,
        "abstract_hashes": abstract_hashes,
    }
    run.stats = stats
    run.status = "done"
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return stats


async def get_effective_spans(db, record: Record) -> list[EffectivePicoSpan]:
    """Load the current hash-matched machine spans and apply human actions."""

    if not record.abstract:
        return []
    machine_spans = await active_machine_spans(db, record)
    human_actions = list(
        (
            await db.scalars(
                select(PicoHuman)
                .where(PicoHuman.record_id == record.id)
                .order_by(PicoHuman.created_at, PicoHuman.id)
            )
        ).all()
    )
    return merge_effective_spans(record.abstract, machine_spans, human_actions)


async def active_machine_spans(db, record: Record) -> list[PicoMachine]:
    """Return spans from the newest completed run matching the current abstract hash."""

    if not record.abstract:
        return []
    runs = await _completed_rule_runs(db)
    run_id = _matching_run_id(runs, record.id, abstract_hash(record.abstract))
    if run_id is None:
        return []
    return list(
        (
            await db.scalars(
                select(PicoMachine)
                .where(PicoMachine.record_id == record.id)
                .where(PicoMachine.rule_version == settings.PICO_RULE_VERSION)
                .where(PicoMachine.run_id == run_id)
                .order_by(PicoMachine.start_char, PicoMachine.end_char, PicoMachine.id)
            )
        ).all()
    )


async def _eligible_records(db, query_version_id: int) -> list[Record]:
    records = list(
        (
            await db.scalars(
                select(Record)
                .join(QueryRecord, QueryRecord.record_id == Record.id)
                .where(QueryRecord.query_version_id == query_version_id)
                .where(Record.abstract.is_not(None))
                .where(Record.abstract != "")
                .order_by(Record.id)
            )
        ).all()
    )
    if not records:
        return []
    decisions = list(
        (
            await db.scalars(
                select(MachineDecision)
                .where(MachineDecision.record_id.in_([record.id for record in records]))
                .where(MachineDecision.rule_version == settings.SCREEN_DECISION_VERSION)
            )
        ).all()
    )
    by_record = {decision.record_id: decision.decision for decision in decisions}
    return [record for record in records if by_record.get(record.id) != "exclude"]


async def _completed_rule_runs(db) -> list[RunLog]:
    return list(
        (
            await db.scalars(
                select(RunLog)
                .where(RunLog.step == "pico", RunLog.status == "done")
                .where(RunLog.rule_version == settings.PICO_RULE_VERSION)
                .order_by(RunLog.id.desc())
            )
        ).all()
    )


def _matching_run_id(runs: list[RunLog], record_id: int, digest: str) -> int | None:
    key = str(record_id)
    return next(
        (
            run.id for run in runs
            if (run.stats or {}).get("abstract_hashes", {}).get(key) == digest
        ),
        None,
    )


def abstract_hash(abstract: str) -> str:
    """Return a short stable hash used only for incremental invalidation."""

    return hashlib.sha256(abstract.encode("utf-8")).hexdigest()[:16]
