"""Order-independent query snapshots, diffs, lineage, and activation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.models import (
    DedupGroup, DedupPair, GroupMember, HumanDecision, MachineDecision,
    QueryRecord, QueryVersion, Record, RunLog,
)
from app.services.dedup_algorithm import choose_representative, connected_components
from app.services.dedup_service import _to_dedup_record


async def lineage(db, query_version_id: int) -> dict:
    """Return the immutable ancestor chain and immediate children."""

    current = await _version(db, query_version_id)
    ancestors = []
    cursor = current
    seen = {current.id}
    while cursor.parent_id is not None:
        cursor = await _version(db, cursor.parent_id)
        if cursor.id in seen:
            raise HTTPException(status_code=409, detail="Query lineage contains a cycle")
        seen.add(cursor.id)
        ancestors.append(cursor)
    ancestors.reverse()
    children = list((await db.scalars(
        select(QueryVersion).where(QueryVersion.parent_id == current.id).order_by(QueryVersion.id)
    )).all())
    return {"current": current, "ancestors": ancestors, "children": children}


async def snapshot(db, query_version_id: int) -> dict:
    """Build a canonical snapshot from persistent records, pairs, and decisions."""

    await _version(db, query_version_id)
    records = list((await db.scalars(
        select(Record).join(QueryRecord).where(
            QueryRecord.query_version_id == query_version_id
        ).order_by(Record.id)
    )).all())
    record_ids = [record.id for record in records]
    pairs = await _pairs(db, record_ids)
    groups = connected_components(record_ids, [
        (pair.record_a_id, pair.record_b_id) for pair in pairs
        if (pair.human_verdict or pair.machine_verdict) == "duplicate"
    ])
    by_id = {record.id: record for record in records}
    human = await _latest_human(db, record_ids)
    effective_statuses = []
    for members in groups:
        values = {human[record_id].decision for record_id in members if record_id in human}
        representative_id = choose_representative([_to_dedup_record(by_id[value]) for value in members])
        if len(values) > 1:
            status, decided_by = "conflict", "human"
        elif values:
            status, decided_by = next(iter(values)), "human"
        else:
            machine = await db.get(MachineDecision, (representative_id, settings.SCREEN_DECISION_VERSION))
            status, decided_by = (machine.decision if machine else "maybe"), "machine"
        effective_statuses.append({
            "members": members,
            "representative_record_id": representative_id,
            "status": status,
            "decided_by": decided_by,
        })
    canonical = {
        "record_ids": record_ids,
        "groups": groups,
        "effective_statuses": effective_statuses,
    }
    signature = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"query_version_id": query_version_id, "signature": signature, **canonical}


async def diff(db, query_version_id: int, against: int | None = None) -> dict:
    """Compare two canonical version snapshots without mutating either version."""

    current = await _version(db, query_version_id)
    against_id = against if against is not None else current.parent_id
    if against_id is None:
        raise HTTPException(status_code=422, detail="against is required for a root query version")
    before, after = await snapshot(db, against_id), await snapshot(db, query_version_id)
    before_ids, after_ids = set(before["record_ids"]), set(after["record_ids"])
    added_ids, removed_ids = sorted(after_ids - before_ids), sorted(before_ids - after_ids)
    human = await _latest_human(db, added_ids)
    machine_decided = set((await db.scalars(
        select(MachineDecision.record_id).where(
            MachineDecision.record_id.in_(added_ids),
            MachineDecision.rule_version == settings.SCREEN_DECISION_VERSION,
        )
    )).all()) if added_ids else set()
    added = [{
        "record_id": value,
        "previously_decided": value in human or value in machine_decided,
    } for value in added_ids]
    old_group_ids = await _materialized_group_ids(db, against_id)
    old_group_by_record = {
        record_id: group_id for group_id, members in old_group_ids.items() for record_id in members
    }
    canonical_old_group = {
        record_id: tuple(group)
        for group in before["groups"] for record_id in group
    }
    newly_merged = []
    for group in after["groups"]:
        prior_groups = sorted({canonical_old_group[value] for value in group if value in canonical_old_group})
        if len(prior_groups) > 1:
            prior_ids = sorted({old_group_by_record[value] for value in group if value in old_group_by_record})
            newly_merged.append({
                "members": group, "old_group_ids": prior_ids,
                "old_groups": [list(value) for value in prior_groups],
            })
    before_conflicts = {tuple(item["members"]) for item in before["effective_statuses"] if item["status"] == "conflict"}
    after_conflicts = [item["members"] for item in after["effective_statuses"] if item["status"] == "conflict" and tuple(item["members"]) not in before_conflicts]
    status_by_record = {
        record_id: item["status"]
        for item in after["effective_statuses"] for record_id in item["members"]
    }
    previously_linked = set((await db.scalars(
        select(QueryRecord.record_id).where(
            QueryRecord.record_id.in_(added_ids),
            QueryRecord.query_version_id != query_version_id,
        ).distinct()
    )).all()) if added_ids else set()
    excluded_reappeared = sorted(
        value for value in added_ids
        if value in previously_linked and (
            status_by_record.get(value) == "exclude"
            or (value in human and human[value].decision == "exclude")
        )
    )
    summary = {
        "added": len(added), "removed": len(removed_ids),
        "unchanged": len(before_ids & after_ids),
        "groups_newly_merged": len(newly_merged),
        "new_conflicts": len(after_conflicts),
        "excluded_reappeared": len(excluded_reappeared),
    }
    return {
        "query_version_id": query_version_id, "against": against_id,
        "added": added, "removed": removed_ids,
        "unchanged_count": len(before_ids & after_ids),
        "groups_newly_merged": newly_merged,
        "new_conflicts": after_conflicts,
        "excluded_reappeared": excluded_reappeared,
        "summary": summary,
    }


async def activate(db, query_version_id: int) -> RunLog:
    """Append an activation marker without modifying a query-version row."""

    await _version(db, query_version_id)
    run = RunLog(
        query_version_id=query_version_id, step="export", rule_version="activation-v1",
        params={"action": "activate"}, stats={"active_query_version_id": query_version_id},
        status="done", finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _version(db, query_version_id: int) -> QueryVersion:
    version = await db.get(QueryVersion, query_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    return version


async def _pairs(db, record_ids: list[int]) -> list[DedupPair]:
    if not record_ids:
        return []
    return list((await db.scalars(
        select(DedupPair).where(
            DedupPair.record_a_id.in_(record_ids), DedupPair.record_b_id.in_(record_ids)
        ).order_by(DedupPair.record_a_id, DedupPair.record_b_id)
    )).all())


async def _latest_human(db, record_ids: list[int]) -> dict[int, HumanDecision]:
    if not record_ids:
        return {}
    rows = list((await db.scalars(
        select(HumanDecision).where(HumanDecision.record_id.in_(record_ids)).order_by(
            HumanDecision.record_id, HumanDecision.created_at.desc(), HumanDecision.id.desc()
        )
    )).all())
    output = {}
    for row in rows:
        output.setdefault(row.record_id, row)
    return output


async def _materialized_group_ids(db, query_version_id: int) -> dict[int, list[int]]:
    run = await db.scalar(
        select(RunLog).where(RunLog.step == "dedup", RunLog.status == "done").where(
            RunLog.query_version_id == query_version_id
        ).order_by(RunLog.id.desc())
    )
    if run is None:
        return {}
    rows = (await db.execute(
        select(GroupMember.group_id, GroupMember.record_id).join(DedupGroup).where(
            DedupGroup.run_id == run.id
        ).order_by(GroupMember.group_id, GroupMember.record_id)
    )).all()
    output: dict[int, list[int]] = {}
    for group_id, record_id in rows:
        output.setdefault(group_id, []).append(record_id)
    return output
