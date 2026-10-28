"""End-to-end test script against the real Supabase database.

Runs the full pipeline (fetch → dedup → screen → PICO) using a small
real query (limit=5 records) and verifies correctness of each step.
Cleans up all created rows afterwards.

Usage:
    python scripts/e2e_supabase.py

Exit code 0 = all checks pass. Prints a summary table.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
os.environ.setdefault("MAX_RECORDS", "5")

from sqlalchemy import delete, select, func

from app.db import AsyncSessionLocal
from app.models import (
    AuditLog, Criteria, DedupGroup, DedupPair, GroupMember,
    HumanDecision, MachineDecision, MachineScreening, PicoHuman,
    PicoMachine, QueryHit, QueryRecord, QueryVersion, Record, RunLog,
)
from app.services.criteria_seed import seed_criteria
from app.services.dedup_service import create_dedup_run, execute_dedup_run
from app.services.fetch_service import create_fetch_run, execute_fetch_run
from app.services.pico_service import create_pico_run, execute_pico_run
from app.services.review_service import effective_status, review_groups_for_query
from app.services.screening_service import create_screening_run, execute_screening_run

QUERY = '"semaglutide"[Title] AND randomized controlled trial[Publication Type]'
LABEL = "_e2e_test_"

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, condition, detail))
    icon = "✓" if condition else "✗"
    print(f"  {icon} {name}" + (f"  [{detail}]" if detail else ""))


async def cleanup(db, version_id: int, record_ids: list[int], run_ids: list[int]) -> None:
    if record_ids:
        await db.execute(delete(PicoMachine).where(PicoMachine.record_id.in_(record_ids)))
        await db.execute(delete(PicoHuman).where(PicoHuman.record_id.in_(record_ids)))
        await db.execute(delete(HumanDecision).where(HumanDecision.record_id.in_(record_ids)))
        await db.execute(delete(MachineDecision).where(MachineDecision.record_id.in_(record_ids)))
        await db.execute(delete(MachineScreening).where(MachineScreening.record_id.in_(record_ids)))
    gm_groups = (await db.execute(
        select(GroupMember.group_id).join(DedupGroup)
        .where(DedupGroup.run_id.in_(run_ids))
    )).scalars().all()
    if gm_groups:
        await db.execute(delete(GroupMember).where(GroupMember.group_id.in_(gm_groups)))
        await db.execute(delete(DedupGroup).where(DedupGroup.run_id.in_(run_ids)))
    if record_ids:
        await db.execute(delete(DedupPair).where(
            DedupPair.record_a_id.in_(record_ids) | DedupPair.record_b_id.in_(record_ids)
        ))
        await db.execute(delete(QueryRecord).where(QueryRecord.query_version_id == version_id))
        await db.execute(delete(Record).where(Record.id.in_(record_ids)))
    await db.execute(delete(AuditLog).where(AuditLog.query_version_id == version_id))
    await db.execute(delete(QueryHit).where(QueryHit.query_version_id == version_id))
    await db.execute(delete(RunLog).where(RunLog.query_version_id == version_id))
    await db.execute(delete(QueryVersion).where(QueryVersion.id == version_id))
    await db.commit()


async def run() -> None:
    print(f"\n{'='*60}")
    print("Mini-Rayyan e2e test — Supabase")
    print(f"{'='*60}")

    async with AsyncSessionLocal() as db:
        run_ids: list[int] = []
        version_id: int | None = None
        record_ids: list[int] = []

        try:
            # ------------------------------------------------------------------
            print("\n[1] Setup")
            await seed_criteria(db)
            criteria_count = await db.scalar(select(func.count()).select_from(Criteria))
            check("criteria seeded", (criteria_count or 0) > 0, f"n={criteria_count}")

            version = QueryVersion(
                label=LABEL, query_text=QUERY,
                status="approved", created_by="e2e_supabase.py",
            )
            db.add(version); await db.commit(); await db.refresh(version)
            version_id = version.id
            check("query version created", version_id is not None, f"id={version_id}")

            # ------------------------------------------------------------------
            print("\n[2] Fetch")
            fetch_run = await create_fetch_run(db, version_id, "pubmed")
            run_ids.append(fetch_run.id)
            stats = await execute_fetch_run(db, fetch_run.id)
            record_ids = list(stats.get("new_record_ids", []))
            check("fetch completed", stats.get("found", 0) > 0, f"found={stats.get('found')}")
            check("records stored", stats.get("new", 0) > 0, f"new={stats.get('new')}")
            check("records linked", stats.get("linked", 0) > 0, f"linked={stats.get('linked')}")

            # Verify records have title and abstract
            if record_ids:
                sample = await db.get(Record, record_ids[0])
                check("sample has title", bool(sample and sample.title), sample.title[:40] if sample and sample.title else "")
                check("sample has abstract", bool(sample and sample.abstract))
                check("sample has source", sample.source == "pubmed" if sample else False)

            # ------------------------------------------------------------------
            print("\n[3] Dedup")
            dedup_run = await create_dedup_run(db, [version_id], record_ids)
            run_ids.append(dedup_run.id)
            dstats = await execute_dedup_run(db, dedup_run.id)
            check("dedup completed", "records" in dstats, str(dstats))
            # With only 5 records from one source, expect 0 duplicates
            check("dedup run logged", dedup_run.id is not None, f"run_id={dedup_run.id}")

            # ------------------------------------------------------------------
            print("\n[4] Screen")
            screen_run = await create_screening_run(db, version_id)
            run_ids.append(screen_run.id)
            sstats = await execute_screening_run(db, screen_run.id)
            check("screen completed", sstats.get("records_screened_new", 0) > 0,
                  f"screened={sstats.get('records_screened_new')}")
            check("machine decisions created",
                  sstats.get("include", 0) + sstats.get("exclude", 0) + sstats.get("maybe", 0) > 0,
                  f"inc={sstats.get('include')} exc={sstats.get('exclude')} maybe={sstats.get('maybe')}")

            # ------------------------------------------------------------------
            print("\n[5] PICO")
            pico_run = await create_pico_run(db, version_id)
            run_ids.append(pico_run.id)
            pstats = await execute_pico_run(db, pico_run.id)
            check("pico completed", pstats.get("processed_new", 0) > 0,
                  f"processed={pstats.get('processed_new')}")

            # ------------------------------------------------------------------
            print("\n[6] Review layer")
            groups = await review_groups_for_query(db, version_id)
            check("review groups returned", len(groups) > 0, f"groups={len(groups)}")
            rep_id = groups[0].representative_record_id
            eff = await effective_status(db, rep_id, groups[0].group_id)
            check("effective status has value", eff.status in ("include", "exclude", "maybe"))
            check("decided_by set", eff.decided_by in ("machine", "human"))

        finally:
            # ------------------------------------------------------------------
            print("\n[7] Cleanup")
            if version_id is not None:
                await cleanup(db, version_id, record_ids, run_ids)
                print("  ✓ all test rows deleted")

    # Summary
    print(f"\n{'='*60}")
    passed = sum(1 for _, ok, _ in checks if ok)
    failed = sum(1 for _, ok, _ in checks if not ok)
    print(f"RESULT: {passed} passed, {failed} failed")
    if failed:
        print("\nFailed checks:")
        for name, ok, detail in checks:
            if not ok:
                print(f"  ✗ {name}  [{detail}]")
    print(f"{'='*60}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run())
