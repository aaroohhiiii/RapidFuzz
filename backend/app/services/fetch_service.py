from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import QueryRecord, QueryVersion, Record, RunLog
from app.services import europepmc, pubmed


async def create_fetch_run(
    db, query_version_id: int, source: str | None = None,
    query_version_ids: list[int] | None = None,
) -> RunLog:
    version = await db.get(QueryVersion, query_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Query version not found")
    if version.status != "approved":
        raise HTTPException(status_code=409, detail="Only approved query versions can be fetched")
    if source not in {None, "pubmed", "europepmc"}:
        raise HTTPException(status_code=422, detail="source must be pubmed or europepmc")

    scope = sorted(set(query_version_ids or []) | {query_version_id})
    known_versions = set((await db.scalars(select(QueryVersion.id).where(QueryVersion.id.in_(scope)))).all())
    if known_versions != set(scope):
        raise HTTPException(status_code=404, detail="Dedup scope contains an unknown query version")
    run = RunLog(
        query_version_id=query_version_id,
        step="fetch",
        params={
            "query_text": version.query_text,
            "date_from": version.date_from.isoformat() if version.date_from else None,
            "date_to": version.date_to.isoformat() if version.date_to else None,
            "source": source or "all",
            "max_records": settings.MAX_RECORDS,
            "query_version_ids": scope,
        },
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def run_fetch_task(run_id: int) -> None:
    async with AsyncSessionLocal() as db:
        try:
            stats = await execute_fetch_run(db, run_id)
            run = await db.get(RunLog, run_id)
            from app.services.dedup_service import create_dedup_run, execute_dedup_run
            from app.services.screening_service import create_screening_run, execute_screening_run
            from app.services.pico_service import create_pico_run, execute_pico_run
            scope = list((run.params or {}).get("query_version_ids") or [run.query_version_id])
            dedup_run = await create_dedup_run(db, scope, stats["new_record_ids"])
            await execute_dedup_run(db, dedup_run.id)
            screen_run = await create_screening_run(db, run.query_version_id)
            await execute_screening_run(db, screen_run.id)
            pico_run = await create_pico_run(db, run.query_version_id)
            await execute_pico_run(db, pico_run.id)
        except Exception as exc:
            await db.rollback()
            run = await db.get(RunLog, run_id)
            if run is not None:
                run.status = "failed"
                run.stats = {"error": str(exc)}
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()


async def execute_fetch_run(db, run_id: int) -> dict[str, Any]:
    run = await db.get(RunLog, run_id)
    if run is None or run.query_version_id is None:
        raise ValueError(f"Fetch run {run_id} not found")
    version = await db.get(QueryVersion, run.query_version_id)
    if version is None:
        raise ValueError(f"Query version {run.query_version_id} not found")

    source = (run.params or {}).get("source", "all")
    limit = int((run.params or {}).get("max_records", settings.MAX_RECORDS))
    found_ids: list[tuple[str, str]] = []
    if source in {"all", "pubmed"}:
        found_ids.extend(("pubmed", value) for value in await pubmed.search_ids(version.query_text, version.date_from, version.date_to, limit=limit))
    remaining = max(0, limit - len(found_ids))
    if source in {"all", "europepmc"} and remaining:
        found_ids.extend(("europepmc", value) for value in await europepmc.search_ids(version.query_text, version.date_from, version.date_to, limit=remaining))

    existing_rows = list((await db.execute(
        select(Record).where(
            tuple_(Record.source, Record.source_id).in_(found_ids)
        )
    )).scalars().all()) if found_ids else []
    existing_by_key = {(record.source, record.source_id): record for record in existing_rows}
    new_ids = {
        source_name: [source_id for found_source, source_id in found_ids if found_source == source_name and (found_source, source_id) not in existing_by_key]
        for source_name in ("pubmed", "europepmc")
    }
    fetched = []
    fetched.extend(await pubmed.fetch_records_by_ids(new_ids["pubmed"]))
    fetched.extend(await europepmc.fetch_records_by_ids(new_ids["europepmc"]))
    fetched_by_key = {(item["source"], str(item["source_id"])): item for item in fetched}

    new_record_ids: list[int] = []
    linked = 0
    for rank, key in enumerate(found_ids, start=1):
        existing_record = existing_by_key.get(key)
        item = fetched_by_key.get(key)
        if existing_record is None and item is None:
            continue
        if existing_record is None:
            record = Record(
                source=item["source"],
                source_id=str(item["source_id"]),
                pmid=item.get("pmid"),
                doi=item.get("doi"),
                title=item.get("title"),
                title_clean=item.get("title_clean"),
                abstract=item.get("abstract"),
                authors=item.get("authors"),
                first_author=item.get("first_author"),
                year=item.get("year"),
                journal=item.get("journal"),
                volume=item.get("volume"),
                pages=item.get("pages"),
                language=item.get("language"),
                pub_types=item.get("pub_types"),
                mesh_terms=item.get("mesh_terms"),
                raw=item.get("raw"),
                first_seen_run=run.id,
            )
            db.add(record)
            await db.flush()
            record_id = record.id
            new_record_ids.append(record_id)
        else:
            record_id = existing_record.id

        insert_fn = sqlite_insert if db.bind.dialect.name == "sqlite" else insert
        stmt = insert_fn(QueryRecord).values(
            query_version_id=version.id,
            record_id=record_id,
            rank_in_results=rank,
        ).on_conflict_do_nothing(index_elements=["query_version_id", "record_id"])
        result = await db.execute(stmt)
        linked += result.rowcount if result.rowcount and result.rowcount > 0 else 0

    run.stats = {
        "found": len(found_ids),
        "new": len(new_record_ids),
        "already_stored": len(existing_by_key),
        "linked": linked,
        "new_record_ids": sorted(new_record_ids),
    }
    run.status = "done"
    run.finished_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(run)
    return {"query_version_id": version.id, **run.stats, "run_id": run.id}
