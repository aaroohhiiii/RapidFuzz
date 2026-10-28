"""Run a disposable end-to-end fetch flow without touching configured data.

The script creates a temporary SQLite database, approves a query version,
creates a fetch run, executes it, and prints persisted row counts and samples.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.models import QueryRecord, QueryVersion, Record
from app.services.fetch_service import create_fetch_run, execute_fetch_run
from tests.db_support import create_test_database


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mini-rayyan-flow-") as directory:
        database = Path(directory) / "flow.sqlite3"
        engine, sessions = await create_test_database(database)

        try:
            async with sessions() as db:
                version = QueryVersion(
                    label="small semaglutide RCT flow",
                    query_text='"semaglutide"[Title] AND randomized controlled trial[Publication Type]',
                    status="draft",
                    created_by="scripts/run_small_flow.py",
                )
                db.add(version)
                await db.commit()
                await db.refresh(version)
                created_status = version.status

                version.status = "approved"
                await db.commit()
                run = await create_fetch_run(db, version.id, source="pubmed")
                accepted = {"run_id": run.id, "query_version_id": version.id, "status": run.status}

                result = await execute_fetch_run(db, run.id)
                record_count = await db.scalar(select(func.count()).select_from(Record))
                link_count = await db.scalar(select(func.count()).select_from(QueryRecord))
                samples = list(
                    (await db.scalars(select(Record.title).order_by(Record.id).limit(3))).all()
                )
                await db.refresh(run)

                print(json.dumps({
                    "database": "temporary/disposable",
                    "max_records": int(os.environ.get("MAX_RECORDS", "250")),
                    "query": version.query_text,
                    "created_status": created_status,
                    "approved_status": version.status,
                    "fetch_accepted": accepted,
                    "run_status": run.status,
                    "run_stats": run.stats,
                    "records_rows": record_count,
                    "query_records_rows": link_count,
                    "sample_titles": samples,
                    "worker_result": result,
                }, indent=2))
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
