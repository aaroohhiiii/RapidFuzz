from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks

from app.models import QueryRecord, QueryVersion, Record
from app.routers.screening import (
    get_record_screening,
    get_screening_summary,
    list_criteria,
    patch_criterion,
    start_screening_run,
)
from app.schemas import CriterionPatch, ScreeningRunRequest
from app.services.criteria_seed import seed_criteria
from app.services.screening_service import execute_screening_run
from tests.db_support import create_test_database


def test_screening_endpoints_return_run_evidence_summary_and_toggle_counts(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "screen-api.sqlite3")
        try:
            async with sessions() as db:
                await seed_criteria(db)
                version = QueryVersion(query_text="test")
                record = Record(
                    source="pubmed",
                    source_id="1",
                    title="Randomized semaglutide trial in adults",
                    abstract="Adult participants received semaglutide or placebo in a randomized trial.",
                    language="eng",
                    pub_types=["Randomized Controlled Trial", "Journal Article"],
                    mesh_terms=["Humans", "Adult"],
                )
                db.add_all([version, record])
                await db.flush()
                db.add(QueryRecord(query_version_id=version.id, record_id=record.id))
                await db.commit()

                tasks = BackgroundTasks()
                accepted = await start_screening_run(
                    ScreeningRunRequest(query_version_id=version.id), tasks, db
                )
                assert accepted.status == "running" and len(tasks.tasks) == 1
                await execute_screening_run(db, accepted.run_id)

                criteria = await list_criteria(db)
                assert len(criteria) == 9
                detail = await get_record_screening(record.id, db)
                assert len(detail.criteria) == 9
                assert detail.machine_decision.decision == "include"
                summary = await get_screening_summary(version.id, db)
                assert summary.include == 1 and summary.exclude == summary.maybe == 0

                topic = next(criterion for criterion in criteria if criterion.code == "topic_match")
                response = await patch_criterion(
                    topic.id,
                    CriterionPatch(active=False, reviewer="reviewer@example.com"),
                    db,
                )
                assert response.criterion.active is False
                assert response.counts.include == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
