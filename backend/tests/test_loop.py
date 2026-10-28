from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.config import settings
from app.models import (
    DedupGroup, DedupPair, GroupMember, HumanDecision, MachineDecision,
    QueryRecord, QueryVersion, Record, RunLog,
)
from app.services import europepmc, pubmed
from app.services.fetch_service import create_fetch_run, execute_fetch_run
from app.services.loop_service import activate, diff, lineage, snapshot
from app.services.dedup_algorithm import DedupRecord, find_candidate_pairs
from app.services.criteria_seed import seed_criteria
from app.services.pico_service import create_pico_run, execute_pico_run
from app.services.screening_service import create_screening_run, execute_screening_run
from tests.db_support import create_test_database


def _record(source_id: str, title: str | None = None) -> Record:
    return Record(
        source="pubmed", source_id=source_id, pmid=source_id,
        title=title or f"Paper {source_id}", title_clean=(title or f"Paper {source_id}").lower(),
        abstract="Adults received semaglutide or placebo. Body weight was measured.",
        authors=["Smith J"], first_author="smith", year=2024,
        journal="Journal", pub_types=[], mesh_terms=[], raw={},
    )


def test_incremental_dedup_only_emits_pairs_touching_new_records():
    records = [
        DedupRecord(id=1, source="pubmed", doi="10/x"),
        DedupRecord(id=2, source="europepmc", doi="10/x"),
        DedupRecord(id=3, source="pubmed", doi="10/x"),
    ]
    pairs = find_candidate_pairs(records, {3})
    assert [(pair.record_a_id, pair.record_b_id) for pair in pairs] == [(1, 3), (2, 3)]


def test_incremental_fetch_only_fetches_new_bodies_and_links_all(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    async def ids(*_args, **_kwargs):
        return ["1", "2", "3"]

    async def bodies(values):
        calls.append(list(values))
        return [
            {**_record(value).__dict__, "source": "pubmed", "source_id": value,
             "pmid": value, "doi": None, "volume": None, "pages": None,
             "language": "eng", "first_seen_run": None, "created_at": None}
            for value in values
        ]

    async def empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(pubmed, "search_ids", ids)
    monkeypatch.setattr(pubmed, "fetch_records_by_ids", bodies)
    monkeypatch.setattr(europepmc, "search_ids", empty)
    monkeypatch.setattr(europepmc, "fetch_records_by_ids", empty)

    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "incremental.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="q", status="approved")
                db.add_all([version, _record("1"), _record("2")])
                await db.commit()
                run = await create_fetch_run(db, version.id, "pubmed")
                stats = await execute_fetch_run(db, run.id)
                assert calls == [["3"]]
                assert stats["found"] == 3 and stats["new"] == 1
                assert stats["already_stored"] == 2 and stats["linked"] == 3
                assert len((await db.scalars(select(QueryRecord))).all()) == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_diff_decisions_reappearance_conflict_and_exact_revert(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "diff.sqlite3")
        try:
            async with sessions() as db:
                v1 = QueryVersion(query_text="v1", status="approved")
                v2 = QueryVersion(query_text="v2", status="approved", parent_id=None)
                v3 = QueryVersion(query_text="v3", status="approved")
                r1, r2, r3 = _record("1"), _record("2"), _record("3")
                db.add_all([v1, v2, v3, r1, r2, r3])
                await db.flush()
                v2.parent_id = v1.id
                v3.parent_id = v2.id
                db.add_all([
                    QueryRecord(query_version_id=v1.id, record_id=r1.id),
                    QueryRecord(query_version_id=v1.id, record_id=r3.id),
                    QueryRecord(query_version_id=v2.id, record_id=r1.id),
                    QueryRecord(query_version_id=v2.id, record_id=r2.id),
                    QueryRecord(query_version_id=v3.id, record_id=r1.id),
                    QueryRecord(query_version_id=v3.id, record_id=r2.id),
                    QueryRecord(query_version_id=v3.id, record_id=r3.id),
                    DedupPair(record_a_id=r1.id, record_b_id=r3.id, method="fuzzy", score=.95, machine_verdict="duplicate"),
                    DedupPair(record_a_id=r2.id, record_b_id=r3.id, method="fuzzy", score=.95, machine_verdict="duplicate"),
                    HumanDecision(record_id=r2.id, decision="exclude", reviewer="A"),
                    HumanDecision(record_id=r1.id, decision="include", reviewer="A"),
                    HumanDecision(record_id=r3.id, decision="exclude", reviewer="B"),
                    MachineDecision(record_id=r1.id, rule_version=settings.SCREEN_DECISION_VERSION, decision="maybe", reasons=[]),
                    MachineDecision(record_id=r2.id, rule_version=settings.SCREEN_DECISION_VERSION, decision="maybe", reasons=[]),
                    MachineDecision(record_id=r3.id, rule_version=settings.SCREEN_DECISION_VERSION, decision="maybe", reasons=[]),
                ])
                old_run = RunLog(
                    query_version_id=v2.id, step="dedup", status="done",
                    params={"query_version_ids": [v2.id]}, stats={},
                )
                db.add(old_run)
                await db.flush()
                old_left = DedupGroup(representative_record_id=r1.id, run_id=old_run.id)
                old_right = DedupGroup(representative_record_id=r2.id, run_id=old_run.id)
                db.add_all([old_left, old_right])
                await db.flush()
                db.add_all([
                    GroupMember(group_id=old_left.id, record_id=r1.id, is_representative=True),
                    GroupMember(group_id=old_right.id, record_id=r2.id, is_representative=True),
                ])
                await db.commit()
                before = await snapshot(db, v1.id)
                result = await diff(db, v3.id)
                assert result["unchanged_count"] == 2
                assert result["excluded_reappeared"] == [r3.id]
                assert result["added"] == [{"record_id": r3.id, "previously_decided": True}]
                assert result["summary"]["groups_newly_merged"] == 1
                assert result["groups_newly_merged"][0]["old_group_ids"] == [old_left.id, old_right.id]
                assert result["summary"]["new_conflicts"] == 1
                assert (await snapshot(db, v1.id))["signature"] == before["signature"]
                tree = await lineage(db, v3.id)
                assert [item.id for item in tree["ancestors"]] == [v1.id, v2.id]
                marker = await activate(db, v1.id)
                assert marker.params == {"action": "activate"}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_snapshot_surfaces_merge_conflict_and_resolution(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "conflict.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="v", status="approved")
                left, right = _record("10"), _record("20")
                db.add_all([version, left, right])
                await db.flush()
                db.add_all([
                    QueryRecord(query_version_id=version.id, record_id=left.id),
                    QueryRecord(query_version_id=version.id, record_id=right.id),
                    DedupPair(record_a_id=left.id, record_b_id=right.id, method="pmid", score=1, machine_verdict="duplicate"),
                    HumanDecision(record_id=left.id, decision="include", reviewer="A"),
                    HumanDecision(record_id=right.id, decision="exclude", reviewer="B"),
                ])
                await db.commit()
                first = await snapshot(db, version.id)
                assert first["effective_statuses"][0]["status"] == "conflict"
                db.add(HumanDecision(record_id=right.id, decision="include", reviewer="B"))
                await db.commit()
                second = await snapshot(db, version.id)
                assert second["effective_statuses"][0]["status"] == "include"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_ten_new_of_one_hundred_only_screens_and_tags_ten(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "ten-new.sqlite3")
        try:
            async with sessions() as db:
                await seed_criteria(db)
                v1 = QueryVersion(query_text="v1", status="approved")
                v2 = QueryVersion(query_text="v2", status="approved")
                records = [_record(str(value)) for value in range(100)]
                db.add_all([v1, v2, *records])
                await db.flush()
                v2.parent_id = v1.id
                db.add_all(QueryRecord(query_version_id=v1.id, record_id=record.id) for record in records[:90])
                db.add_all(QueryRecord(query_version_id=v2.id, record_id=record.id) for record in records)
                await db.commit()

                screen1 = await create_screening_run(db, v1.id)
                await execute_screening_run(db, screen1.id)
                pico1 = await create_pico_run(db, v1.id)
                await execute_pico_run(db, pico1.id)

                screen2 = await create_screening_run(db, v2.id)
                screen_stats = await execute_screening_run(db, screen2.id)
                pico2 = await create_pico_run(db, v2.id)
                pico_stats = await execute_pico_run(db, pico2.id)

                assert screen_stats["records_screened_new"] == 10
                assert pico_stats["processed_new"] == 10
                assert pico_stats["skipped_unchanged"] == 90
        finally:
            await engine.dispose()

    asyncio.run(scenario())
