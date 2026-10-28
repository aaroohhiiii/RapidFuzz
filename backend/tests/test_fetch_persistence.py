from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy import func, select
from app.models import QueryRecord, QueryVersion, Record
from app.routers.records import get_record, list_query_records
from app.routers.runs import get_run
from app.services import europepmc, pubmed
from app.services.fetch_service import create_fetch_run, execute_fetch_run
from tests.db_support import create_test_database


SAMPLE_RECORD = {
    "source": "pubmed",
    "source_id": "12345678",
    "pmid": "12345678",
    "doi": "10.1000/example",
    "title": "A small randomized trial",
    "title_clean": "a small randomized trial",
    "abstract": "Background: Test record.",
    "authors": ["Smith Jane"],
    "first_author": "smith",
    "year": 2024,
    "journal": "Example Journal",
    "volume": "1",
    "pages": "1-2",
    "language": "eng",
    "pub_types": ["Randomized Controlled Trial"],
    "mesh_terms": [],
    "raw": {"fixture": True},
}


async def _database(tmp_path):
    return await create_test_database(tmp_path / "isolated.sqlite3")


def test_fetch_rejects_non_approved_version(tmp_path):
    async def scenario():
        engine, sessions = await _database(tmp_path)
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="draft query", status="draft")
                db.add(version)
                await db.commit()
                await db.refresh(version)
                try:
                    await create_fetch_run(db, version.id)
                except HTTPException as exc:
                    assert exc.status_code == 409
                else:
                    raise AssertionError("draft query was accepted for fetch")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_fetching_same_version_twice_is_idempotent(tmp_path, monkeypatch):
    async def fake_pubmed_ids(*_args, **_kwargs):
        return ["12345678"]

    async def fake_pubmed_records(ids):
        return [SAMPLE_RECORD.copy()] if ids else []

    async def fake_empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(pubmed, "search_ids", fake_pubmed_ids)
    monkeypatch.setattr(pubmed, "fetch_records_by_ids", fake_pubmed_records)
    monkeypatch.setattr(europepmc, "search_ids", fake_empty)
    monkeypatch.setattr(europepmc, "fetch_records_by_ids", fake_empty)

    async def scenario():
        engine, sessions = await _database(tmp_path)
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="small query", status="approved")
                db.add(version)
                await db.commit()
                await db.refresh(version)

                first = await create_fetch_run(db, version.id)
                await execute_fetch_run(db, first.id)
                second = await create_fetch_run(db, version.id)
                await execute_fetch_run(db, second.id)

                assert await db.scalar(select(func.count()).select_from(Record)) == 1
                assert await db.scalar(select(func.count()).select_from(QueryRecord)) == 1
                assert second.stats == {
                    "found": 1, "new": 0, "already_stored": 1,
                    "linked": 0, "new_record_ids": [],
                }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_overlapping_versions_share_record_but_keep_both_links(tmp_path, monkeypatch):
    async def fake_pubmed_ids(*_args, **_kwargs):
        return ["12345678"]

    async def fake_pubmed_records(ids):
        return [SAMPLE_RECORD.copy()] if ids else []

    async def fake_empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(pubmed, "search_ids", fake_pubmed_ids)
    monkeypatch.setattr(pubmed, "fetch_records_by_ids", fake_pubmed_records)
    monkeypatch.setattr(europepmc, "search_ids", fake_empty)
    monkeypatch.setattr(europepmc, "fetch_records_by_ids", fake_empty)

    async def scenario():
        engine, sessions = await _database(tmp_path)
        try:
            async with sessions() as db:
                versions = [
                    QueryVersion(query_text="query one", status="approved"),
                    QueryVersion(query_text="query two", status="approved"),
                ]
                db.add_all(versions)
                await db.commit()
                for version in versions:
                    await db.refresh(version)
                    run = await create_fetch_run(db, version.id)
                    await execute_fetch_run(db, run.id)

                assert await db.scalar(select(func.count()).select_from(Record)) == 1
                assert await db.scalar(select(func.count()).select_from(QueryRecord)) == 2
                linked_versions = set((await db.scalars(select(QueryRecord.query_version_id))).all())
                assert linked_versions == {version.id for version in versions}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_read_endpoints_return_record_page_record_and_run(tmp_path):
    async def scenario():
        engine, sessions = await _database(tmp_path)
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="endpoint query", status="approved")
                db.add(version)
                await db.commit()
                await db.refresh(version)
                run = await create_fetch_run(db, version.id, "pubmed")
                record = Record(**SAMPLE_RECORD, first_seen_run=run.id)
                db.add(record)
                await db.flush()
                db.add(QueryRecord(query_version_id=version.id, record_id=record.id, rank_in_results=1))
                await db.commit()

                page = await list_query_records(version.id, limit=1, offset=0, db=db)
                assert page.total == 1
                assert page.limit == 1 and page.offset == 0
                assert page.items[0].id == record.id
                assert (await get_record(record.id, db)).source_id == "12345678"
                assert (await get_run(run.id, db)).id == run.id
        finally:
            await engine.dispose()

    asyncio.run(scenario())
