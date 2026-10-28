from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
import io

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import settings
from app.models import (
    AuditLog,
    DedupGroup,
    GroupMember,
    HumanDecision,
    MachineDecision,
    PicoMachine,
    QueryRecord,
    QueryVersion,
    Record,
    RunLog,
)
from app.routers.review import (
    export_included_csv,
    get_record_history,
    list_audit,
    list_conflicts,
    list_review_records,
)
from app.schemas import HumanDecisionRequest, PicoHumanRequest, UndoDecisionRequest
from app.services.pico_service import abstract_hash
from app.services.review_service import (
    add_human_decision,
    add_pico_human_action,
    effective_status,
    resolve_group,
    undo_human_decision,
)
from tests.db_support import create_test_database


async def _review_fixture(tmp_path):
    engine, sessions = await create_test_database(tmp_path / "review.sqlite3")
    db = sessions()
    query = QueryVersion(query_text="review query", status="approved")
    other_query = QueryVersion(query_text="other query", status="approved")
    representative = Record(
        source="pubmed",
        source_id="pm-1",
        pmid="100",
        doi="10.1/test",
        title='Semaglutide, "quoted" trial',
        abstract="Adults with obesity received semaglutide or placebo. Weight loss was measured.",
        authors=["Smith J", "Lee K"],
        year=2024,
        journal="Example Journal",
    )
    hidden = Record(
        source="europepmc",
        source_id="epmc-1",
        pmid="100",
        title="Duplicate source record",
        abstract="Adults with obesity received semaglutide or placebo.",
        year=2024,
    )
    db.add_all([query, other_query, representative, hidden])
    await db.flush()
    db.add_all([
        QueryRecord(query_version_id=query.id, record_id=representative.id),
        QueryRecord(query_version_id=query.id, record_id=hidden.id),
        QueryRecord(query_version_id=other_query.id, record_id=hidden.id),
    ])
    dedup_run = RunLog(
        query_version_id=query.id,
        step="dedup",
        rule_version="dedup-v1",
        params={"query_version_ids": [query.id, other_query.id]},
        stats={},
        status="done",
        finished_at=datetime.now(timezone.utc),
    )
    db.add(dedup_run)
    await db.flush()
    group = DedupGroup(representative_record_id=representative.id, run_id=dedup_run.id)
    db.add(group)
    await db.flush()
    db.add_all([
        GroupMember(group_id=group.id, record_id=representative.id, is_representative=True),
        GroupMember(group_id=group.id, record_id=hidden.id, is_representative=False),
    ])
    screen_run = RunLog(
        query_version_id=query.id,
        step="screen",
        rule_version="screen-v1",
        params={},
        stats={},
        status="done",
        finished_at=datetime.now(timezone.utc),
    )
    db.add(screen_run)
    await db.flush()
    db.add(MachineDecision(
        record_id=representative.id,
        rule_version=settings.SCREEN_DECISION_VERSION,
        decision="exclude",
        reasons=["simulation"],
        run_id=screen_run.id,
    ))
    pico_run = RunLog(
        query_version_id=query.id,
        step="pico",
        rule_version=settings.PICO_RULE_VERSION,
        params={},
        stats={"abstract_hashes": {str(representative.id): abstract_hash(representative.abstract)}},
        status="done",
        finished_at=datetime.now(timezone.utc),
    )
    db.add(pico_run)
    await db.flush()
    intervention_start = representative.abstract.index("semaglutide")
    db.add(PicoMachine(
        record_id=representative.id,
        label="I",
        start_char=intervention_start,
        end_char=intervention_start + len("semaglutide"),
        span_text="semaglutide",
        method="rules",
        confidence=0.95,
        rule_version=settings.PICO_RULE_VERSION,
        run_id=pico_run.id,
    ))
    await db.commit()
    return engine, db, query, other_query, representative, hidden, group


def test_hidden_human_decision_beats_machine_and_later_machine_rerun(tmp_path):
    async def scenario():
        engine, db, query, _, representative, hidden, group = await _review_fixture(tmp_path)
        try:
            assert (await effective_status(db, representative.id, group.id)).status == "exclude"
            result = await add_human_decision(
                db, hidden.id, "include", "eligible", "checked duplicate", "alice"
            )
            assert result.status == "include" and result.decided_by == "human"

            machine = await db.get(
                MachineDecision, (representative.id, settings.SCREEN_DECISION_VERSION)
            )
            machine.decision = "maybe"
            machine.reasons = ["rerun_changed"]
            await db.commit()
            assert (await effective_status(db, representative.id, group.id)).status == "include"

            page = await list_review_records(
                query.id, None, None, None, None, None, "record_id", 50, 0, False, db
            )
            assert page.total == 1 and page.items[0].effective.status == "include"
            assert page.items[0].group_size == 2 and page.items[0].hidden is False
            hidden_page = await list_review_records(
                query.id, None, None, None, None, None, "record_id", 50, 0, True, db
            )
            assert hidden_page.total == 2 and sum(row.hidden for row in hidden_page.items) == 1

            audit = await db.scalar(select(AuditLog).where(AuditLog.action == "decision"))
            assert audit.before_value is None
            assert audit.after_value["decision"] == "include"
            audit_page = await list_audit("record", str(hidden.id), "alice", 50, 0, db)
            assert audit_page.total == 1 and audit_page.items[0].actor == "alice"
        finally:
            await db.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_conflict_appears_and_group_resolution_clears_it(tmp_path):
    async def scenario():
        engine, db, query, _, representative, hidden, group = await _review_fixture(tmp_path)
        try:
            await add_human_decision(db, representative.id, "exclude", "not eligible", None, "alice")
            await add_human_decision(db, hidden.id, "include", "eligible", None, "bob")
            conflicts = await list_conflicts(query.id, db)
            assert len(conflicts) == 1 and conflicts[0].group_id == group.id

            resolved = await resolve_group(db, group.id, "maybe", "needs consensus", "resolved", "carol")
            assert resolved.status == "maybe"
            assert await list_conflicts(query.id, db) == []
            latest = list(
                (
                    await db.scalars(
                        select(HumanDecision)
                        .where(HumanDecision.record_id.in_([representative.id, hidden.id]))
                        .order_by(HumanDecision.id.desc())
                    )
                ).all()
            )
            assert {row.decision for row in latest[:2]} == {"maybe"}
        finally:
            await db.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_undo_reinserts_previous_human_decision_and_audits(tmp_path):
    async def scenario():
        engine, db, _, _, representative, _, _ = await _review_fixture(tmp_path)
        try:
            await add_human_decision(db, representative.id, "include", "first", "one", "alice")
            await add_human_decision(db, representative.id, "exclude", "second", "two", "alice")
            result = await undo_human_decision(db, representative.id, "bob")
            assert result.status == "include" and result.reasons == ["first"]
            undo_audit = await db.scalar(select(AuditLog).where(AuditLog.action == "decision_undo"))
            assert undo_audit.before_value["decision"] == "exclude"
            assert undo_audit.after_value["decision"] == "include"
        finally:
            await db.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_first_decision_undo_reports_pending_restore_machine_migration(tmp_path):
    async def scenario():
        engine, db, _, _, representative, _, _ = await _review_fixture(tmp_path)
        try:
            await add_human_decision(db, representative.id, "include", None, None, "alice")
            with pytest.raises(HTTPException) as error:
                await undo_human_decision(db, representative.id, "bob")
            assert error.value.status_code == 409
            assert "001_human_decision_restore_machine.sql" in str(error.value.detail)
        finally:
            await db.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_pico_write_validates_offsets_and_history_merges_events(tmp_path):
    async def scenario():
        engine, db, _, _, representative, _, _ = await _review_fixture(tmp_path)
        try:
            start = representative.abstract.index("Weight loss")
            await add_pico_human_action(
                db, representative.id, "add", "O", start, start + len("Weight loss"), None, "alice"
            )
            with pytest.raises(HTTPException) as error:
                await add_pico_human_action(
                    db, representative.id, "add", "O", -1, 500, None, "alice"
                )
            assert error.value.status_code == 422
            history = await get_record_history(representative.id, db)
            assert {event.event_type for event in history.events}.issuperset({"machine_decision", "pico_machine", "pico_human"})
            audit = await db.scalar(select(AuditLog).where(AuditLog.action == "pico_edit"))
            assert audit.before_value is None and audit.after_value["span_text"] == "Weight loss"
        finally:
            await db.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_included_csv_has_bom_quoting_group_sources_queries_and_pico(tmp_path):
    async def scenario():
        engine, db, query, other_query, representative, hidden, _ = await _review_fixture(tmp_path)
        try:
            await add_human_decision(db, hidden.id, "include", "eligible", "CSV note", "alice")
            response = await export_included_csv(query.id, db)
            assert response.body.startswith(b"\xef\xbb\xbf")
            rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))))
            assert len(rows) == 1
            row = rows[0]
            assert row["record_id"] == str(representative.id)
            assert row["title"] == 'Semaglutide, "quoted" trial'
            assert row["decision"] == "include" and row["decided_by"] == "human"
            assert row["intervention"] == "semaglutide"
            assert row["found_by_query_versions"] == f"{query.id} | {other_query.id}"
            assert row["other_source_ids"] == f"europepmc:{hidden.source_id}"
            assert row["authors"] == "Smith J | Lee K"
        finally:
            await db.close()
            await engine.dispose()

    asyncio.run(scenario())
