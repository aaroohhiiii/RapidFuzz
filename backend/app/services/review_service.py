"""Shared effective review status, decisions, conflicts, history, and export data."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.config import settings
from app.models import (
    AuditLog,
    DedupGroup,
    DedupPair,
    GroupMember,
    HumanDecision,
    MachineDecision,
    MachineScreening,
    PicoHuman,
    PicoMachine,
    QueryRecord,
    Record,
    RunLog,
)
from app.services.pico_service import get_effective_spans


@dataclass(frozen=True)
class EffectiveStatus:
    status: str
    decided_by: str
    reasons: list[str]
    note: str | None
    reviewer: str | None
    decided_at: datetime | None
    representative_record_id: int
    group_id: int | None


@dataclass(frozen=True)
class ReviewGroup:
    group_id: int | None
    representative_record_id: int
    member_ids: tuple[int, ...]


async def effective_status(db, record_id: int, group_id: int | None = None) -> EffectiveStatus:
    """Return the shared effective status for API, UI data, and CSV export."""

    group = await _group_for_record(db, record_id, group_id)
    latest = await latest_human_decisions(db, list(group.member_ids))
    values = {decision.decision for decision in latest.values()}
    if len(values) > 1:
        return EffectiveStatus(
            status="conflict",
            decided_by="human",
            reasons=["member_decisions_disagree"],
            note=None,
            reviewer=None,
            decided_at=max((decision.created_at for decision in latest.values()), default=None),
            representative_record_id=group.representative_record_id,
            group_id=group.group_id,
        )
    if len(values) == 1:
        selected = max(latest.values(), key=lambda decision: (decision.created_at, decision.id))
        return EffectiveStatus(
            status=selected.decision,
            decided_by="human",
            reasons=[selected.reason] if selected.reason else [],
            note=selected.note,
            reviewer=selected.reviewer,
            decided_at=selected.created_at,
            representative_record_id=group.representative_record_id,
            group_id=group.group_id,
        )
    machine = await db.get(
        MachineDecision,
        (group.representative_record_id, settings.SCREEN_DECISION_VERSION),
    )
    machine_run = await db.get(RunLog, machine.run_id) if machine and machine.run_id else None
    return EffectiveStatus(
        status=machine.decision if machine else "maybe",
        decided_by="machine",
        reasons=list(machine.reasons or ["not_screened"]) if machine else ["not_screened"],
        note=None,
        reviewer=None,
        decided_at=(machine_run.finished_at or machine_run.started_at) if machine_run else None,
        representative_record_id=group.representative_record_id,
        group_id=group.group_id,
    )


async def review_groups_for_query(db, query_version_id: int) -> list[ReviewGroup]:
    """Return current dedup groups intersecting a query, with singleton fallback."""

    query_record_ids = set(
        (await db.scalars(select(QueryRecord.record_id).where(QueryRecord.query_version_id == query_version_id))).all()
    )
    if not query_record_ids:
        return []
    run = await _latest_dedup_run(db, query_version_id)
    if run is None:
        return [ReviewGroup(None, record_id, (record_id,)) for record_id in sorted(query_record_ids)]
    groups = list((await db.scalars(select(DedupGroup).where(DedupGroup.run_id == run.id))).all())
    memberships = list(
        (
            await db.scalars(
                select(GroupMember)
                .join(DedupGroup, DedupGroup.id == GroupMember.group_id)
                .where(DedupGroup.run_id == run.id)
                .order_by(GroupMember.group_id, GroupMember.record_id)
            )
        ).all()
    )
    by_group: dict[int, list[int]] = {}
    for membership in memberships:
        by_group.setdefault(membership.group_id, []).append(membership.record_id)
    result = [
        ReviewGroup(group.id, group.representative_record_id, tuple(by_group.get(group.id, [])))
        for group in groups
        if query_record_ids.intersection(by_group.get(group.id, []))
    ]
    covered = {record_id for group in result for record_id in group.member_ids}
    result.extend(
        ReviewGroup(None, record_id, (record_id,))
        for record_id in sorted(query_record_ids - covered)
    )
    return sorted(result, key=lambda group: group.representative_record_id)


async def add_human_decision(
    db,
    record_id: int,
    decision: str,
    reason: str | None,
    note: str | None,
    reviewer: str,
) -> EffectiveStatus:
    """Append a record decision and its audit row in one transaction."""

    if await db.get(Record, record_id) is None:
        raise HTTPException(status_code=404, detail="Record not found")
    group = await _group_for_record(db, record_id, None)
    previous = await _latest_decision_for_record(db, record_id)
    human = HumanDecision(
        record_id=record_id,
        group_id_at_time=group.group_id,
        decision=decision,
        reason=reason,
        note=note,
        reviewer=reviewer,
    )
    db.add(human)
    await db.flush()
    db.add(_audit(
        actor=reviewer,
        action="decision",
        entity_type="record",
        entity_id=str(record_id),
        before=_decision_dict(previous),
        after=_decision_dict(human),
    ))
    await db.commit()
    return await effective_status(db, record_id, group.group_id)


async def undo_human_decision(db, record_id: int, reviewer: str) -> EffectiveStatus:
    """Append the prior human value; first-decision restore awaits migration approval."""

    history = list(
        (
            await db.scalars(
                select(HumanDecision)
                .where(HumanDecision.record_id == record_id)
                .order_by(HumanDecision.created_at.desc(), HumanDecision.id.desc())
            )
        ).all()
    )
    if not history:
        raise HTTPException(status_code=409, detail="No human decision to undo")
    if len(history) == 1:
        raise HTTPException(
            status_code=409,
            detail="Restoring machine control requires proposed migration 001_human_decision_restore_machine.sql",
        )
    current, previous = history[0], history[1]
    group = await _group_for_record(db, record_id, None)
    restored = HumanDecision(
        record_id=record_id,
        group_id_at_time=group.group_id,
        decision=previous.decision,
        reason=previous.reason,
        note=previous.note,
        reviewer=reviewer,
    )
    db.add(restored)
    await db.flush()
    db.add(_audit(
        actor=reviewer,
        action="decision_undo",
        entity_type="record",
        entity_id=str(record_id),
        before=_decision_dict(current),
        after=_decision_dict(restored),
    ))
    await db.commit()
    return await effective_status(db, record_id, group.group_id)


async def add_pico_human_action(
    db,
    record_id: int,
    action: str,
    label: str | None,
    start_char: int | None,
    end_char: int | None,
    machine_span_id: int | None,
    reviewer: str,
):
    """Validate and append a PICO correction with an audit row."""

    record = await db.get(Record, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    abstract = record.abstract or ""
    machine = await db.get(PicoMachine, machine_span_id) if machine_span_id is not None else None
    if action in {"remove", "edit"} and (machine is None or machine.record_id != record_id):
        raise HTTPException(status_code=422, detail="A valid machine_span_id for this record is required")
    if action in {"add", "edit"}:
        if label is None or start_char is None or end_char is None:
            raise HTTPException(status_code=422, detail="label, start_char and end_char are required")
        if not (0 <= start_char < end_char <= len(abstract)):
            raise HTTPException(status_code=422, detail="PICO offsets are outside the abstract")
        span_text = abstract[start_char:end_char]
    else:
        label = start_char = end_char = None
        span_text = None
    human = PicoHuman(
        record_id=record_id,
        action=action,
        machine_span_id=machine_span_id,
        label=label,
        start_char=start_char,
        end_char=end_char,
        span_text=span_text,
        reviewer=reviewer,
    )
    db.add(human)
    await db.flush()
    db.add(_audit(
        actor=reviewer,
        action="pico_edit",
        entity_type="pico_span",
        entity_id=str(machine_span_id or human.id),
        before=_machine_span_dict(machine),
        after={
            "action": action,
            "label": label,
            "start_char": start_char,
            "end_char": end_char,
            "span_text": span_text,
        },
    ))
    await db.commit()
    return human


async def latest_human_decisions(db, record_ids: list[int]) -> dict[int, HumanDecision]:
    if not record_ids:
        return {}
    rows = list(
        (
            await db.scalars(
                select(HumanDecision)
                .where(HumanDecision.record_id.in_(record_ids))
                .order_by(HumanDecision.record_id, HumanDecision.created_at.desc(), HumanDecision.id.desc())
            )
        ).all()
    )
    latest: dict[int, HumanDecision] = {}
    for row in rows:
        latest.setdefault(row.record_id, row)
    return latest


async def resolve_group(
    db, group_id: int, decision: str, reason: str | None, note: str | None, reviewer: str
) -> EffectiveStatus:
    """Append one identical human decision per member and audit every record."""

    group = await db.get(DedupGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    member_ids = list(
        (await db.scalars(select(GroupMember.record_id).where(GroupMember.group_id == group_id))).all()
    )
    previous = await latest_human_decisions(db, member_ids)
    for record_id in member_ids:
        human = HumanDecision(
            record_id=record_id,
            group_id_at_time=group_id,
            decision=decision,
            reason=reason,
            note=note,
            reviewer=reviewer,
        )
        db.add(human)
        await db.flush()
        db.add(_audit(
            actor=reviewer,
            action="group_resolve",
            entity_type="record",
            entity_id=str(record_id),
            before=_decision_dict(previous.get(record_id)),
            after=_decision_dict(human),
        ))
    await db.commit()
    return await effective_status(db, group.representative_record_id, group_id)


async def record_history(db, record_id: int) -> list[dict[str, Any]]:
    """Merge machine and human review events into one timestamped timeline."""

    if await db.get(Record, record_id) is None:
        raise HTTPException(status_code=404, detail="Record not found")
    events: list[dict[str, Any]] = []
    runs = {run.id: run for run in (await db.scalars(select(RunLog))).all()}
    screenings = list((await db.scalars(select(MachineScreening).where(MachineScreening.record_id == record_id))).all())
    decisions = list((await db.scalars(select(MachineDecision).where(MachineDecision.record_id == record_id))).all())
    pairs = list(
        (
            await db.scalars(
                select(DedupPair).where(or_(DedupPair.record_a_id == record_id, DedupPair.record_b_id == record_id))
            )
        ).all()
    )
    machine_pico = list((await db.scalars(select(PicoMachine).where(PicoMachine.record_id == record_id))).all())
    human_decisions = list((await db.scalars(select(HumanDecision).where(HumanDecision.record_id == record_id))).all())
    human_pico = list((await db.scalars(select(PicoHuman).where(PicoHuman.record_id == record_id))).all())
    for row in screenings:
        _append_run_event(events, runs, row.run_id, "criterion_result", asdict_safe(row))
    for row in decisions:
        _append_run_event(events, runs, row.run_id, "machine_decision", asdict_safe(row))
    for row in pairs:
        _append_run_event(events, runs, row.run_id, "dedup_verdict", asdict_safe(row))
        if row.human_verdict and row.human_at:
            events.append({
                "timestamp": row.human_at,
                "event_type": "dedup_human_verdict",
                "source": "human",
                "data": {
                    "record_a_id": row.record_a_id,
                    "record_b_id": row.record_b_id,
                    "human_verdict": row.human_verdict,
                    "human_by": row.human_by,
                },
            })
    for row in machine_pico:
        _append_run_event(events, runs, row.run_id, "pico_machine", asdict_safe(row))
    events.extend({
        "timestamp": row.created_at,
        "event_type": "human_decision",
        "source": "human",
        "data": asdict_safe(row),
    } for row in human_decisions)
    events.extend({
        "timestamp": row.created_at,
        "event_type": "pico_human",
        "source": "human",
        "data": asdict_safe(row),
    } for row in human_pico)
    return sorted(events, key=lambda event: event["timestamp"])


async def _group_for_record(db, record_id: int, group_id: int | None) -> ReviewGroup:
    if group_id is not None:
        group = await db.get(DedupGroup, group_id)
    else:
        group = await db.scalar(
            select(DedupGroup)
            .join(GroupMember, GroupMember.group_id == DedupGroup.id)
            .join(RunLog, RunLog.id == DedupGroup.run_id)
            .where(GroupMember.record_id == record_id, RunLog.status == "done")
            .order_by(RunLog.id.desc(), DedupGroup.id.desc())
            .limit(1)
        )
    if group is None:
        return ReviewGroup(None, record_id, (record_id,))
    member_ids = tuple(
        (await db.scalars(select(GroupMember.record_id).where(GroupMember.group_id == group.id).order_by(GroupMember.record_id))).all()
    )
    return ReviewGroup(group.id, group.representative_record_id, member_ids)


async def _latest_dedup_run(db, query_version_id: int) -> RunLog | None:
    runs = list(
        (
            await db.scalars(
                select(RunLog)
                .where(RunLog.step == "dedup", RunLog.status == "done")
                .order_by(RunLog.id.desc())
            )
        ).all()
    )
    return next(
        (run for run in runs if query_version_id in (run.params or {}).get("query_version_ids", [])),
        None,
    )


async def _latest_decision_for_record(db, record_id: int) -> HumanDecision | None:
    return await db.scalar(
        select(HumanDecision)
        .where(HumanDecision.record_id == record_id)
        .order_by(HumanDecision.created_at.desc(), HumanDecision.id.desc())
        .limit(1)
    )


def _audit(actor: str, action: str, entity_type: str, entity_id: str, before, after) -> AuditLog:
    return AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_value=before,
        after_value=after,
    )


def _decision_dict(decision: HumanDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "id": decision.id,
        "decision": decision.decision,
        "reason": decision.reason,
        "note": decision.note,
        "reviewer": decision.reviewer,
    }


def _machine_span_dict(span: PicoMachine | None) -> dict[str, Any] | None:
    if span is None:
        return None
    return {
        "id": span.id,
        "label": span.label,
        "start_char": span.start_char,
        "end_char": span.end_char,
        "span_text": span.span_text,
    }


def asdict_safe(row) -> dict[str, Any]:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"raw"}
    }


def _append_run_event(events, runs, run_id, event_type, data) -> None:
    run = runs.get(run_id)
    if run is not None:
        events.append({
            "timestamp": run.finished_at or run.started_at,
            "event_type": event_type,
            "source": "machine",
            "data": data,
        })
