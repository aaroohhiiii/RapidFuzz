"""Print current per-criterion and combined screening counts for a query version."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Criteria, MachineDecision, MachineScreening, QueryRecord
from app.services.screening_rules import rule_versions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query-version-id", type=int, required=True)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    async with AsyncSessionLocal() as db:
        record_ids = list(
            (
                await db.scalars(
                    select(QueryRecord.record_id)
                    .where(QueryRecord.query_version_id == args.query_version_id)
                    .order_by(QueryRecord.record_id)
                )
            ).all()
        )
        criteria = list((await db.scalars(select(Criteria).order_by(Criteria.id))).all())
        versions = rule_versions()
        rows = [] if not record_ids else list(
            (
                await db.scalars(
                    select(MachineScreening).where(MachineScreening.record_id.in_(record_ids))
                )
            ).all()
        )
        criterion_counts = {}
        for criterion in criteria:
            counts = Counter(
                row.result
                for row in rows
                if row.criterion_id == criterion.id
                and row.rule_version == versions.get(criterion.code)
            )
            criterion_counts[criterion.code] = {
                "active": criterion.active,
                "rule_version": versions.get(criterion.code),
                "hit": counts["hit"],
                "no_hit": counts["no_hit"],
                "unknown": counts["unknown"],
            }
        decisions = [] if not record_ids else list(
            (
                await db.scalars(
                    select(MachineDecision)
                    .where(MachineDecision.record_id.in_(record_ids))
                    .where(MachineDecision.rule_version == settings.SCREEN_DECISION_VERSION)
                )
            ).all()
        )
        decision_counts = Counter(decision.decision for decision in decisions)
        print(json.dumps({
            "query_version_id": args.query_version_id,
            "records": len(record_ids),
            "criteria": criterion_counts,
            "decisions": {
                "include": decision_counts["include"],
                "exclude": decision_counts["exclude"],
                "maybe": decision_counts["maybe"],
            },
        }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
