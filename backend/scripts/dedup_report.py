"""Print exact, fuzzy, and uncertain pair counts from a completed dedup run."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import DedupPair, QueryRecord, RunLog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--run-id", type=int, help="Completed dedup run to report")
    target.add_argument("--query-version-id", type=int, help="Latest completed run containing this version")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    async with AsyncSessionLocal() as db:
        run = await _select_run(db, args.run_id, args.query_version_id)
        scope = [int(value) for value in (run.params or {}).get("query_version_ids", [])]
        record_ids = list(
            (
                await db.scalars(
                    select(QueryRecord.record_id)
                    .where(QueryRecord.query_version_id.in_(scope))
                    .distinct()
                )
            ).all()
        )
        pairs = [] if not record_ids else list(
            (
                await db.scalars(
                    select(DedupPair)
                    .where(DedupPair.record_a_id.in_(record_ids))
                    .where(DedupPair.record_b_id.in_(record_ids))
                )
            ).all()
        )
        exact = sum(
            pair.method in {"doi", "pmid"} and _effective(pair) == "duplicate" for pair in pairs
        )
        fuzzy = sum(pair.method == "fuzzy" and _effective(pair) == "duplicate" for pair in pairs)
        uncertain = sum(_effective(pair) == "uncertain" for pair in pairs)
        print(json.dumps({
            "run_id": run.id,
            "query_version_ids": scope,
            "records": len(record_ids),
            "exact_only_duplicate_pairs": exact,
            "fuzzy_additional_duplicate_pairs": fuzzy,
            "exact_plus_fuzzy_duplicate_pairs": exact + fuzzy,
            "uncertain_pairs": uncertain,
        }, indent=2))


async def _select_run(db, run_id: int | None, query_version_id: int | None) -> RunLog:
    if run_id is not None:
        run = await db.get(RunLog, run_id)
        if run is None or run.step != "dedup" or run.status != "done":
            raise SystemExit(f"No completed dedup run with id {run_id}")
        return run

    runs = list(
        (
            await db.scalars(
                select(RunLog)
                .where(RunLog.step == "dedup", RunLog.status == "done")
                .order_by(RunLog.id.desc())
            )
        ).all()
    )
    if query_version_id is not None:
        runs = [
            run for run in runs
            if query_version_id in (run.params or {}).get("query_version_ids", [])
        ]
    if not runs:
        raise SystemExit("No matching completed dedup run")
    return runs[0]


def _effective(pair: DedupPair) -> str:
    return pair.human_verdict or pair.machine_verdict


if __name__ == "__main__":
    asyncio.run(main())
