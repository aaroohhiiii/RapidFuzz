from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.config import settings
from app.models import AuditLog, Criteria, MachineDecision, MachineScreening, QueryRecord, QueryVersion, Record
from app.services.criteria_seed import seed_criteria
from app.services.screening_service import create_screening_run, execute_screening_run, toggle_criterion
from tests.db_support import create_test_database


def _eligible_record(source_id: str = "1", **overrides) -> Record:
    values = {
        "source": "pubmed",
        "source_id": source_id,
        "title": "Randomized semaglutide trial in adults",
        "title_clean": "randomized semaglutide trial in adults",
        "abstract": "Adult participants were randomized to semaglutide or placebo in this clinical trial.",
        "language": "eng",
        "pub_types": ["Randomized Controlled Trial", "Journal Article"],
        "mesh_terms": ["Humans", "Adult"],
    }
    values.update(overrides)
    return Record(**values)


def test_screening_run_is_idempotent_and_screens_every_record(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "screen.sqlite3")
        try:
            async with sessions() as db:
                await seed_criteria(db)
                version = QueryVersion(query_text="test")
                db.add(version)
                await db.flush()
                records = [_eligible_record("1"), _eligible_record("2")]
                db.add_all(records)
                await db.flush()
                db.add_all(QueryRecord(query_version_id=version.id, record_id=record.id) for record in records)
                await db.commit()

                first = await create_screening_run(db, version.id)
                first_stats = await execute_screening_run(db, first.id)
                second = await create_screening_run(db, version.id)
                second_stats = await execute_screening_run(db, second.id)

                assert first_stats["records"] == 2
                assert first_stats["screened_new"] == 18
                assert first_stats["skipped"] == 0
                assert first_stats["include"] == 2
                assert second_stats["screened_new"] == 0
                assert second_stats["skipped"] == 18
                assert await db.scalar(select(func.count()).select_from(MachineScreening)) == 18
                assert await db.scalar(select(func.count()).select_from(MachineDecision)) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_criteria_seed_is_idempotent_and_preserves_active_choice(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "seed.sqlite3")
        try:
            async with sessions() as db:
                assert await seed_criteria(db) == 9
                simulation = await db.scalar(select(Criteria).where(Criteria.code == "simulation"))
                simulation.active = False
                await db.commit()
                assert await seed_criteria(db) == 9

                assert await db.scalar(select(func.count()).select_from(Criteria)) == 9
                simulation = await db.scalar(select(Criteria).where(Criteria.code == "simulation"))
                assert simulation.active is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_rule_version_bump_reruns_only_that_rule(tmp_path, monkeypatch):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "version.sqlite3")
        try:
            async with sessions() as db:
                await seed_criteria(db)
                version = QueryVersion(query_text="test")
                record = _eligible_record()
                db.add_all([version, record])
                await db.flush()
                db.add(QueryRecord(query_version_id=version.id, record_id=record.id))
                await db.commit()

                first = await create_screening_run(db, version.id)
                await execute_screening_run(db, first.id)
                monkeypatch.setattr(settings, "SCREEN_TOPIC_MATCH_VERSION", "topic-match-v2")
                second = await create_screening_run(db, version.id)
                stats = await execute_screening_run(db, second.id)

                assert stats["screened_new"] == 1
                assert stats["skipped"] == 8
                assert await db.scalar(select(func.count()).select_from(MachineScreening)) == 10
                versions = set(
                    (
                        await db.scalars(
                            select(MachineScreening.rule_version)
                            .where(MachineScreening.record_id == record.id)
                        )
                    ).all()
                )
                assert {"topic-match-v1", "topic-match-v2"}.issubset(versions)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_toggle_recomputes_decisions_without_rerunning_rules_and_writes_audit(tmp_path):
    async def scenario():
        engine, sessions = await create_test_database(tmp_path / "toggle.sqlite3")
        try:
            async with sessions() as db:
                await seed_criteria(db)
                version = QueryVersion(query_text="test")
                record = _eligible_record(
                    title="Randomized semaglutide simulation in adults",
                    abstract="Adult participants were randomized to semaglutide or placebo in a simulation.",
                )
                db.add_all([version, record])
                await db.flush()
                db.add(QueryRecord(query_version_id=version.id, record_id=record.id))
                await db.commit()

                run = await create_screening_run(db, version.id)
                await execute_screening_run(db, run.id)
                decision = await db.get(MachineDecision, (record.id, settings.SCREEN_DECISION_VERSION))
                assert decision.decision == "exclude" and decision.reasons == ["simulation"]
                initial_screening_count = await db.scalar(select(func.count()).select_from(MachineScreening))

                simulation = await db.scalar(select(Criteria).where(Criteria.code == "simulation"))
                criterion, recomputed_runs, counts = await toggle_criterion(
                    db, simulation.id, False, "reviewer@example.com"
                )
                decision = await db.get(MachineDecision, (record.id, settings.SCREEN_DECISION_VERSION))

                assert criterion.active is False
                assert len(recomputed_runs) == 1
                assert decision.decision == "include" and decision.reasons == []
                assert counts["include"] == 1 and counts["exclude"] == 0
                assert await db.scalar(select(func.count()).select_from(MachineScreening)) == initial_screening_count
                assert await db.scalar(select(func.count()).select_from(AuditLog)) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
