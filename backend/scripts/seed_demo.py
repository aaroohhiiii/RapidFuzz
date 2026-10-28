"""Offline demo seed script.

Loads canned records from tests/fixtures into the configured database without
hitting any external API.  Safe to run multiple times (idempotent on
source+source_id).  Creates one approved query version and links all records
to it, then runs dedup → screen → PICO in-process.

Usage:
    python scripts/seed_demo.py [--db-url postgresql+asyncpg://...]

After running, start the backend with `uvicorn app.main:app --reload` and
the frontend with `npm run dev` in frontend/; everything is pre-populated.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

DEMO_QUERY = (
    "(semaglutide OR liraglutide OR tirzepatide OR \"GLP-1 receptor agonist\") "
    "AND (obesity OR \"body weight\" OR BMI) "
    "AND (randomized OR randomised OR \"clinical trial\")"
)


def _parse_args() -> str | None:
    parser = argparse.ArgumentParser(description="Seed demo data without hitting any external API.")
    parser.add_argument("--db-url", default=None, help="Override DATABASE_URL for this run only.")
    args = parser.parse_args()
    return args.db_url


async def main() -> None:
    db_url = _parse_args()
    if db_url:
        os.environ["DATABASE_URL"] = db_url

    # Import after possible env override so the engine picks up the right URL
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy import select

    from app.db import AsyncSessionLocal, engine
    from app.models import (
        QueryRecord, QueryVersion, Record,
        RunLog,
    )
    from app.services.criteria_seed import seed_criteria
    from app.services.dedup_service import create_dedup_run, execute_dedup_run
    from app.services.pico_service import create_pico_run, execute_pico_run
    from app.services.pubmed import parse_efetch_xml
    from app.services.europepmc import parse_core_response
    from app.services.normalize import normalize_doi, normalize_title, extract_first_author
    from app.services.screening_service import create_screening_run, execute_screening_run

    print("=== Mini-Rayyan demo seed ===")

    async with AsyncSessionLocal() as db:
        # 1. Seed criteria (idempotent)
        await seed_criteria(db)
        print("✓ criteria seeded")

        # 2. Create / reuse demo query version
        existing = (await db.scalars(
            select(QueryVersion).where(QueryVersion.label == "Demo: GLP-1 RCTs")
        )).first()
        if existing:
            version = existing
            print(f"✓ reusing query version id={version.id}")
        else:
            version = QueryVersion(
                label="Demo: GLP-1 RCTs",
                query_text=DEMO_QUERY,
                status="fetched",
                created_by="seed_demo.py",
            )
            db.add(version)
            await db.commit()
            await db.refresh(version)
            print(f"✓ created query version id={version.id}")

        # 3. Load records from fixtures
        records_data: list[dict] = []

        pubmed_xml = (FIXTURES / "pubmed_efetch.xml").read_bytes()
        records_data.extend(parse_efetch_xml(pubmed_xml))

        epmc_json = json.loads((FIXTURES / "europepmc_core.json").read_text())
        records_data.extend(parse_core_response(epmc_json))

        print(f"  fixture records to load: {len(records_data)}")

        # 4. Insert records (idempotent via UNIQUE(source, source_id))
        is_sqlite = engine.dialect.name == "sqlite"
        insert_fn = sqlite_insert if is_sqlite else pg_insert

        # We need a dummy run_log row for first_seen_run
        run_seed = RunLog(
            query_version_id=version.id, step="fetch",
            params={"source": "fixture"}, stats={},
            status="done",
        )
        db.add(run_seed)
        await db.flush()

        new_count = 0
        all_record_ids: list[int] = []
        for item in records_data:
            source_id = str(item.get("source_id") or "")
            if not source_id:
                continue
            existing_r = (await db.scalars(
                select(Record).where(
                    Record.source == item["source"],
                    Record.source_id == source_id,
                )
            )).first()
            if existing_r:
                all_record_ids.append(existing_r.id)
                continue
            record = Record(
                source=item["source"],
                source_id=source_id,
                pmid=item.get("pmid"),
                doi=item.get("doi"),
                title=item.get("title"),
                title_clean=item.get("title_clean") or normalize_title(item.get("title")),
                abstract=item.get("abstract"),
                authors=item.get("authors") or [],
                first_author=item.get("first_author") or extract_first_author(item.get("authors") or []),
                year=item.get("year"),
                journal=item.get("journal"),
                volume=item.get("volume"),
                pages=item.get("pages"),
                language=item.get("language"),
                pub_types=item.get("pub_types") or [],
                mesh_terms=item.get("mesh_terms") or [],
                raw=item.get("raw") or {},
                first_seen_run=run_seed.id,
            )
            db.add(record)
            await db.flush()
            all_record_ids.append(record.id)
            new_count += 1

        print(f"✓ records: {new_count} new, {len(all_record_ids) - new_count} already stored")

        # 5. Link records to query version
        linked = 0
        for rank, record_id in enumerate(all_record_ids, start=1):
            stmt = insert_fn(QueryRecord).values(
                query_version_id=version.id, record_id=record_id, rank_in_results=rank
            ).on_conflict_do_nothing(index_elements=["query_version_id", "record_id"])
            result = await db.execute(stmt)
            linked += 1 if (result.rowcount or 0) > 0 else 0
        print(f"✓ linked {linked} records to version {version.id}")

        run_seed.stats = {"new": new_count, "found": len(all_record_ids)}
        await db.commit()

        # 6. Dedup
        dedup_run = await create_dedup_run(db, [version.id], all_record_ids)
        dstats = await execute_dedup_run(db, dedup_run.id)
        print(f"✓ dedup: {dstats}")

        # 7. Screen
        screen_run = await create_screening_run(db, version.id)
        sstats = await execute_screening_run(db, screen_run.id)
        print(f"✓ screen: {sstats}")

        # 8. PICO
        pico_run = await create_pico_run(db, version.id)
        pstats = await execute_pico_run(db, pico_run.id)
        print(f"✓ pico: {pstats}")

    print("\n=== Seed complete ===")
    print(f"  Query version id: {version.id}")
    print("  Start backend: uvicorn app.main:app --reload")
    print("  Open frontend: npm run dev (in frontend/)")


if __name__ == "__main__":
    asyncio.run(main())
