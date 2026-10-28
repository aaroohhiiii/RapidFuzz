from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.config import settings
from app.models import MachineDecision, PicoMachine, QueryRecord, QueryVersion, Record
from app.services.pico_service import active_machine_spans, create_pico_run, execute_pico_run
from tests.db_support import create_test_database


def test_pico_run_skips_excluded_is_idempotent_and_retags_changed_abstract(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "pico.sqlite3")
        try:
            async with sessions() as db:
                version = QueryVersion(query_text="test")
                tagged = Record(
                    source="pubmed", source_id="1",
                    abstract="METHODS: Adults with obesity were randomized to semaglutide 2.4 mg or placebo. RESULTS: The primary outcome was change in body weight.",
                )
                none = Record(source="pubmed", source_id="2", abstract="No relevant concepts were reported here.")
                excluded = Record(source="pubmed", source_id="3", abstract="Adults received liraglutide or placebo.")
                db.add_all([version, tagged, none, excluded])
                await db.flush()
                db.add_all(QueryRecord(query_version_id=version.id, record_id=record.id) for record in (tagged, none, excluded))
                db.add_all([
                    MachineDecision(record_id=tagged.id, rule_version=settings.SCREEN_DECISION_VERSION, decision="include", reasons=[]),
                    MachineDecision(record_id=none.id, rule_version=settings.SCREEN_DECISION_VERSION, decision="maybe", reasons=[]),
                    MachineDecision(record_id=excluded.id, rule_version=settings.SCREEN_DECISION_VERSION, decision="exclude", reasons=["simulation"]),
                ])
                await db.commit()

                first = await create_pico_run(db, version.id)
                first_stats = await execute_pico_run(db, first.id)
                first_count = await db.scalar(select(func.count()).select_from(PicoMachine))
                second = await create_pico_run(db, version.id)
                second_stats = await execute_pico_run(db, second.id)

                assert first_stats["records_tagged"] == 2
                assert first_stats["processed_new"] == 2
                assert first_stats["records_with_all_four"] == 1
                assert first_stats["records_with_none"] == 1
                assert second_stats["processed_new"] == 0
                assert second_stats["skipped_unchanged"] == 2
                assert await db.scalar(select(func.count()).select_from(PicoMachine)) == first_count
                assert not await db.scalar(
                    select(func.count()).select_from(PicoMachine).where(PicoMachine.record_id == excluded.id)
                )

                tagged.abstract += " Secondary outcome was waist circumference."
                await db.commit()
                third = await create_pico_run(db, version.id)
                third_stats = await execute_pico_run(db, third.id)
                current = await active_machine_spans(db, tagged)

                assert third_stats["processed_new"] == 1
                assert third_stats["skipped_unchanged"] == 1
                assert await db.scalar(select(func.count()).select_from(PicoMachine)) > first_count
                assert all(tagged.abstract[span.start_char:span.end_char] == span.span_text for span in current)
                assert any(span.label == "O" and span.span_text == "waist circumference" for span in current)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
