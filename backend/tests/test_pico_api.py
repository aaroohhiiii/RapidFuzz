from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks

from app.config import settings
from app.models import MachineDecision, PicoHuman, PicoMachine, QueryRecord, QueryVersion, Record
from app.routers.pico import get_record_pico, start_pico_run
from app.schemas import PicoRunRequest
from app.services.pico_service import execute_pico_run
from tests.db_support import create_test_database


def test_pico_endpoints_queue_run_and_return_effective_sources(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "pico-api.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="test")
                record = Record(
                    source="pubmed", source_id="1",
                    abstract="Adults with obesity received semaglutide or placebo. Weight loss was measured.",
                )
                db.add_all([version, record])
                await db.flush()
                db.add(QueryRecord(query_version_id=version.id, record_id=record.id))
                db.add(MachineDecision(
                    record_id=record.id, rule_version=settings.SCREEN_DECISION_VERSION,
                    decision="include", reasons=[],
                ))
                await db.commit()

                tasks = BackgroundTasks()
                accepted = await start_pico_run(PicoRunRequest(query_version_id=version.id), tasks, db)
                assert accepted.status == "running" and len(tasks.tasks) == 1
                await execute_pico_run(db, accepted.run_id)
                machine = await db.scalar(
                    __import__("sqlalchemy").select(PicoMachine)
                    .where(PicoMachine.record_id == record.id, PicoMachine.label == "I")
                )
                db.add(PicoHuman(
                    record_id=record.id, action="remove", machine_span_id=machine.id, reviewer="reviewer"
                ))
                outcome_start = record.abstract.index("Weight loss")
                db.add(PicoHuman(
                    record_id=record.id, action="add", label="O",
                    start_char=outcome_start, end_char=outcome_start + len("Weight loss"),
                    span_text="Weight loss", reviewer="reviewer",
                ))
                await db.commit()

                response = await get_record_pico(record.id, db)
                assert any(span.source == "human" and span.span_text == "Weight loss" for span in response.spans)
                assert not any(span.source == "machine" and span.id == machine.id for span in response.spans)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
