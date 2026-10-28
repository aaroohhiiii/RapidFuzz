from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks

from app.models import QueryRecord, QueryVersion, Record
from app.routers.dedup import (
    list_dedup_groups,
    list_dedup_pairs,
    set_pair_verdict,
    start_dedup_run,
)
from app.schemas import DedupRunRequest, DedupVerdictRequest
from app.services.dedup_service import execute_dedup_run
from tests.db_support import create_test_database


def test_dedup_endpoints_expose_runs_groups_pairs_and_override_result(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "api.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="endpoint test", status="approved")
                db.add(version)
                await db.flush()
                records = [
                    Record(
                        source="pubmed", source_id="1", doi="10.1/same",
                        title="First", title_clean="first", abstract="long abstract",
                        first_author="smith", year=2024, pub_types=["Journal Article"],
                    ),
                    Record(
                        source="europepmc", source_id="2", doi="10.1/same",
                        title="Second", title_clean="second", abstract="abstract",
                        first_author="smith", year=2024, pub_types=["Journal Article"],
                    ),
                ]
                db.add_all(records)
                await db.flush()
                db.add_all(QueryRecord(query_version_id=version.id, record_id=record.id) for record in records)
                await db.commit()

                tasks = BackgroundTasks()
                accepted = await start_dedup_run(
                    DedupRunRequest(query_version_ids=[version.id]), tasks, db
                )
                assert accepted.status == "running" and accepted.query_version_ids == [version.id]
                assert len(tasks.tasks) == 1
                await execute_dedup_run(db, accepted.run_id)

                groups = await list_dedup_groups(version.id, True, 10, 0, db)
                assert groups.total == 1
                assert len(groups.items[0].members) == 2
                assert len(groups.items[0].pair_evidence) == 1

                pairs = await list_dedup_pairs("duplicate", version.id, 10, 0, db)
                assert pairs.total == 1
                assert pairs.items[0].record_a.id == records[0].id
                assert pairs.items[0].record_b.id == records[1].id

                response = await set_pair_verdict(
                    records[0].id,
                    records[1].id,
                    DedupVerdictRequest(verdict="distinct", reviewer="reviewer@example.com"),
                    db,
                )
                assert len(response.groups) == 2
                assert all(len(group.members) == 1 for group in response.groups)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
