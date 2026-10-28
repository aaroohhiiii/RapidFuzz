"""Idempotently seed the Step 0 screening criteria."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import AsyncSessionLocal
from app.services.criteria_seed import seed_criteria


async def main() -> None:
    async with AsyncSessionLocal() as db:
        count = await seed_criteria(db)
        print(f"Seeded or refreshed {count} criteria without changing active flags.")


if __name__ == "__main__":
    asyncio.run(main())
