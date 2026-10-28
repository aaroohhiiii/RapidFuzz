from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.models import AuditLog, DedupGroup, DedupPair, GroupMember, QueryRecord, QueryVersion, Record
from app.services.dedup_service import apply_human_verdict, create_dedup_run, execute_dedup_run
from tests.db_support import create_test_database


def _record(source_id: str, **overrides) -> Record:
    values = {
        "source": "pubmed",
        "source_id": source_id,
        "title": "Semaglutide for obesity",
        "title_clean": "semaglutide for obesity randomized controlled trial",
        "abstract": "A" * 200,
        "first_author": "smith",
        "year": 2024,
        "journal": "Obesity",
        "pages": "10-20",
        "pub_types": ["Journal Article"],
    }
    values.update(overrides)
    return Record(**values)


def test_materializes_singletons_and_duplicates_without_deleting_records(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "dedup.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="test", status="approved")
                db.add(version)
                await db.flush()
                records = [
                    _record("1", doi="10.1/same"),
                    _record("2", source="europepmc", doi="10.1/same"),
                    _record("3", title_clean="unrelated record", first_author="jones"),
                ]
                db.add_all(records)
                await db.flush()
                db.add_all(QueryRecord(query_version_id=version.id, record_id=item.id) for item in records)
                await db.commit()

                before = await db.scalar(select(func.count()).select_from(Record))
                run = await create_dedup_run(db, [version.id])
                stats = await execute_dedup_run(db, run.id)
                after = await db.scalar(select(func.count()).select_from(Record))

                assert stats == {
                    "records": 3,
                    "pairs_exact": 1,
                    "pairs_fuzzy_dup": 0,
                    "pairs_uncertain": 0,
                    "groups": 2,
                    "hidden": 1,
                }
                assert before == after == 3
                assert await db.scalar(select(func.count()).select_from(DedupGroup)) == 2
                assert await db.scalar(select(func.count()).select_from(GroupMember)) == 3
                assert await db.scalar(
                    select(func.count()).select_from(GroupMember).where(GroupMember.is_representative.is_(True))
                ) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_same_run_is_idempotent_and_scope_union_is_deterministic(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "union.sqlite3")
        try:
            async with sessions() as db:
                versions = [QueryVersion(query_text="one"), QueryVersion(query_text="two")]
                db.add_all(versions)
                await db.flush()
                records = [_record("1", pmid="99"), _record("2", source="europepmc", pmid="99")]
                db.add_all(records)
                await db.flush()
                db.add_all([
                    QueryRecord(query_version_id=versions[0].id, record_id=records[0].id),
                    QueryRecord(query_version_id=versions[1].id, record_id=records[1].id),
                ])
                await db.commit()

                run = await create_dedup_run(db, [versions[1].id, versions[0].id, versions[0].id])
                first = await execute_dedup_run(db, run.id)
                group_count = await db.scalar(select(func.count()).select_from(DedupGroup))
                second = await execute_dedup_run(db, run.id)

                assert run.params["query_version_ids"] == sorted(version.id for version in versions)
                assert first == second
                assert await db.scalar(select(func.count()).select_from(DedupGroup)) == group_count == 1
                pair = await db.scalar(select(DedupPair))
                assert pair.method == "pmid" and pair.machine_verdict == "duplicate"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def _group_signature(db, run_id: int):
    groups = list(
        (await db.scalars(select(DedupGroup).where(DedupGroup.run_id == run_id).order_by(DedupGroup.id))).all()
    )
    signature = []
    for group in groups:
        member_ids = tuple(
            (
                await db.scalars(
                    select(GroupMember.record_id)
                    .where(GroupMember.group_id == group.id)
                    .order_by(GroupMember.record_id)
                )
            ).all()
        )
        signature.append((member_ids, group.representative_record_id))
    return signature


def test_human_distinct_survives_machine_rerun(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "human-distinct.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="test")
                db.add(version)
                await db.flush()
                records = [_record("1", doi="10.1/same"), _record("2", doi="10.1/same")]
                db.add_all(records)
                await db.flush()
                db.add_all(QueryRecord(query_version_id=version.id, record_id=item.id) for item in records)
                await db.commit()

                first_run = await create_dedup_run(db, [version.id])
                await execute_dedup_run(db, first_run.id)
                rebuild_id, _ = await apply_human_verdict(
                    db, records[0].id, records[1].id, "distinct", "reviewer@example.com"
                )
                assert len(await _group_signature(db, rebuild_id)) == 2

                rerun = await create_dedup_run(db, [version.id])
                await execute_dedup_run(db, rerun.id)
                pair = await db.get(DedupPair, (records[0].id, records[1].id))
                assert pair.machine_verdict == "duplicate"
                assert pair.human_verdict == "distinct"
                assert len(await _group_signature(db, rerun.id)) == 2
                assert await db.scalar(select(func.count()).select_from(AuditLog)) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_human_duplicate_merges_uncertain_companion_pair(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "human-duplicate.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="test")
                db.add(version)
                await db.flush()
                records = [
                    _record("1", pub_types=["Journal Article"]),
                    _record("2", pub_types=["Meeting Abstract"]),
                ]
                db.add_all(records)
                await db.flush()
                db.add_all(QueryRecord(query_version_id=version.id, record_id=item.id) for item in records)
                await db.commit()

                run = await create_dedup_run(db, [version.id])
                await execute_dedup_run(db, run.id)
                pair = await db.get(DedupPair, (records[0].id, records[1].id))
                assert pair.machine_verdict == "uncertain"
                assert len(await _group_signature(db, run.id)) == 2

                rebuild_id, touched_ids = await apply_human_verdict(
                    db, records[0].id, records[1].id, "duplicate", "reviewer@example.com"
                )
                assert len(touched_ids) == 1
                assert await _group_signature(db, rebuild_id) == [
                    ((records[0].id, records[1].id), records[0].id)
                ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_new_bridge_record_merges_prior_groups_and_rerun_is_deterministic(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "bridge.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="test")
                db.add(version)
                await db.flush()
                records = [
                    _record("1", doi="10.1/a", pmid="11", title_clean="alpha", first_author="alpha"),
                    _record("2", doi="10.1/a", pmid="22", title_clean="beta", first_author="beta"),
                    _record("3", doi="10.1/b", pmid="22", title_clean="gamma", first_author="gamma"),
                ]
                db.add_all(records)
                await db.flush()
                db.add_all([
                    QueryRecord(query_version_id=version.id, record_id=records[0].id),
                    QueryRecord(query_version_id=version.id, record_id=records[2].id),
                ])
                await db.commit()

                first = await create_dedup_run(db, [version.id])
                await execute_dedup_run(db, first.id)
                assert len(await _group_signature(db, first.id)) == 2

                db.add(QueryRecord(query_version_id=version.id, record_id=records[1].id))
                await db.commit()
                second = await create_dedup_run(db, [version.id])
                await execute_dedup_run(db, second.id)
                third = await create_dedup_run(db, [version.id])
                await execute_dedup_run(db, third.id)

                expected = [((records[0].id, records[1].id, records[2].id), records[0].id)]
                assert await _group_signature(db, second.id) == expected
                assert await _group_signature(db, third.id) == expected
        finally:
            await engine.dispose()

    asyncio.run(scenario())
