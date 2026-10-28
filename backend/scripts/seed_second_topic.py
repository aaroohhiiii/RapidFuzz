"""Seed a second topic for metrics comparison: SGLT2 inhibitors in Heart Failure.

Creates a query version, inserts 5 records (including a cross-database duplicate
and animal/non-primary exclusion cases), and runs dedup -> screen -> pico.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import AsyncSessionLocal, engine
from app.models import (
    QueryRecord, QueryVersion, Record, RunLog
)
from app.services.criteria_seed import seed_criteria
from app.services.dedup_service import create_dedup_run, execute_dedup_run
from app.services.pico_service import create_pico_run, execute_pico_run
from app.services.normalize import normalize_title, extract_first_author
from app.services.screening_service import create_screening_run, execute_screening_run

QUERY_TEXT = '(empagliflozin OR dapagliflozin) AND ("heart failure" OR HFrEF) AND (randomized OR trial)'
LABEL = "SGLT2 inhibitors in Heart Failure"

TOPIC2_RECORDS = [
    {
        "source": "pubmed",
        "source_id": "32865377",
        "pmid": "32865377",
        "doi": "10.1056/nejmoa2022190",
        "title": "Cardiovascular and Renal Outcomes with Empagliflozin in Heart Failure",
        "abstract": "Background: Sodium-glucose cotransporter 2 (SGLT2) inhibitors reduce the risk of hospitalization for heart failure in patients with type 2 diabetes. Methods: In this double-blind trial, we randomly assigned 3730 patients with class II, III, or IV heart failure and an ejection fraction of 40% or less to receive empagliflozin (10 mg once daily) or placebo, in addition to recommended therapy. The primary outcome was a composite of cardiovascular death or hospitalization for worsening heart failure. Results: During a median of 16 months, a primary outcome event occurred in 361 of 1863 patients (19.4%) in the empagliflozin group and in 462 of 1867 patients (24.7%) in the placebo group (hazard ratio for cardiovascular death or hospitalization for heart failure, 0.75; 95% confidence interval, 0.65 to 0.86; P<0.001). Conclusions: Among patients receiving recommended therapy for heart failure, those in the empagliflozin group had a lower risk of cardiovascular death or hospitalization for heart failure than those in the placebo group.",
        "authors": ["Packer M", "Anker SD", "Butler J", "Filippatos G"],
        "year": 2020,
        "journal": "N Engl J Med",
        "volume": "383",
        "pages": "1413-1424",
        "language": "eng",
        "pub_types": ["Randomized Controlled Trial", "Clinical Trial"],
        "mesh_terms": ["Heart Failure", "Empagliflozin", "Sodium-Glucose Transporter 2 Inhibitors"],
    },
    {
        "source": "pubmed",
        "source_id": "31535829",
        "pmid": "31535829",
        "doi": "10.1056/nejmoa1911303",
        "title": "Dapagliflozin in Patients with Heart Failure and Reduced Ejection Fraction",
        "abstract": "Background: In patients with type 2 diabetes, inhibitors of sodium-glucose cotransporter 2 (SGLT2) reduce the risk of first hospitalization for heart failure. Methods: In this phase 3, placebo-controlled trial, we randomly assigned 4744 patients with New York Heart Association class II, III, or IV heart failure and an ejection fraction of 40% or less to receive either dapagliflozin (at a dose of 10 mg once daily) or placebo, in addition to standard care. The primary outcome was a composite of worsening heart failure or cardiovascular death. Results: Over a median of 18.2 months, the primary outcome occurred in 386 of 2373 patients (16.3%) in the dapagliflozin group and in 502 of 2371 patients (21.2%) in the placebo group (hazard ratio, 0.74; 95% CI, 0.65 to 0.85; P<0.001). Conclusions: In patients with heart failure and reduced ejection fraction, the risk of worsening heart failure or death from cardiovascular causes was lower among those who received dapagliflozin than among those who received placebo.",
        "authors": ["McMurray JJV", "Solomon SD", "Inzucchi SE", "Køber L"],
        "year": 2019,
        "journal": "N Engl J Med",
        "volume": "381",
        "pages": "1995-2008",
        "language": "eng",
        "pub_types": ["Randomized Controlled Trial", "Clinical Trial"],
        "mesh_terms": ["Heart Failure", "Dapagliflozin", "Sodium-Glucose Transporter 2 Inhibitors"],
    },
    {
        "source": "europepmc",
        "source_id": "PMC7116666",
        "pmid": "31535829",
        "doi": "10.1056/nejmoa1911303",
        "title": "Dapagliflozin in Patients with Heart Failure and Reduced Ejection Fraction",
        "abstract": "Background: In patients with type 2 diabetes, inhibitors of sodium-glucose cotransporter 2 (SGLT2) reduce the risk of first hospitalization for heart failure. Methods: In this phase 3, placebo-controlled trial, we randomly assigned 4744 patients with New York Heart Association class II, III, or IV heart failure and an ejection fraction of 40% or less to receive either dapagliflozin (at a dose of 10 mg once daily) or placebo, in addition to standard care. The primary outcome was a composite of worsening heart failure or cardiovascular death. Results: Over a median of 18.2 months, the primary outcome occurred in 386 of 2373 patients (16.3%) in the dapagliflozin group and in 502 of 2371 patients (21.2%) in the placebo group (hazard ratio, 0.74; 95% CI, 0.65 to 0.85; P<0.001). Conclusions: In patients with heart failure and reduced ejection fraction, the risk of worsening heart failure or death from cardiovascular causes was lower among those who received dapagliflozin than among those who received placebo.",
        "authors": ["McMurray JJV", "Solomon SD", "Inzucchi SE"],
        "year": 2019,
        "journal": "N Engl J Med",
        "volume": "381",
        "pages": "1995-2008",
        "language": "eng",
        "pub_types": ["Randomized Controlled Trial"],
        "mesh_terms": ["Heart Failure", "Dapagliflozin"],
    },
    {
        "source": "pubmed",
        "source_id": "99990001",
        "pmid": "99990001",
        "doi": "10.1016/j.ejphar.2021.174000",
        "title": "Renal protective mechanisms of empagliflozin in diabetic rats",
        "abstract": "We investigated the renal hemodynamics and tubular transport alterations in Sprague-Dawley male rats with streptozotocin-induced diabetes receiving empagliflozin or vehicle. Animal models demonstrated substantial reduction in renal hyperfiltration.",
        "authors": ["Smith AB", "Jones CD"],
        "year": 2021,
        "journal": "Eur J Pharmacol",
        "volume": "890",
        "pages": "174000",
        "language": "eng",
        "pub_types": ["Comparative Study"],
        "mesh_terms": ["Rats, Sprague-Dawley", "Animals", "Empagliflozin"],
    },
    {
        "source": "pubmed",
        "source_id": "99990002",
        "pmid": "99990002",
        "doi": "10.1038/s41569-020-00400-0",
        "title": "SGLT2 inhibitors in heart failure: clinical mechanisms and trial evidence",
        "abstract": "This narrative review summarizes the molecular mechanisms, hemodynamic effects, and landmark cardiovascular outcome trials of sodium-glucose cotransporter 2 inhibitors across the ejection fraction spectrum.",
        "authors": ["Taylor EF", "Williams GH"],
        "year": 2020,
        "journal": "Nat Rev Cardiol",
        "volume": "17",
        "pages": "560-572",
        "language": "eng",
        "pub_types": ["Review"],
        "mesh_terms": ["Review", "Heart Failure"],
    },
]


async def seed_second_topic() -> int:
    is_sqlite = engine.dialect.name == "sqlite"
    insert_fn = sqlite_insert if is_sqlite else pg_insert

    async with AsyncSessionLocal() as db:
        await seed_criteria(db)

        # 1. Create or get QueryVersion
        existing = (await db.scalars(
            select(QueryVersion).where(QueryVersion.label == LABEL)
        )).first()

        if existing:
            version = existing
            print(f"✓ Reusing existing version id={version.id} ({version.label})")
        else:
            version = QueryVersion(
                label=LABEL,
                query_text=QUERY_TEXT,
                status="fetched",
                created_by="seed_second_topic.py",
            )
            db.add(version)
            await db.commit()
            await db.refresh(version)
            print(f"✓ Created query version id={version.id} ({version.label})")

        # 2. Dummy run_log row for fetch
        run_fetch = RunLog(
            query_version_id=version.id,
            step="fetch",
            params={"source": "all", "query_text": QUERY_TEXT},
            stats={"new": len(TOPIC2_RECORDS), "found": len(TOPIC2_RECORDS)},
            status="done",
        )
        db.add(run_fetch)
        await db.flush()

        # 3. Insert records
        record_ids: list[int] = []
        for item in TOPIC2_RECORDS:
            existing_r = (await db.scalars(
                select(Record).where(
                    Record.source == item["source"],
                    Record.source_id == item["source_id"],
                )
            )).first()
            if existing_r:
                record_ids.append(existing_r.id)
                continue
            rec = Record(
                source=item["source"],
                source_id=item["source_id"],
                pmid=item.get("pmid"),
                doi=item.get("doi"),
                title=item["title"],
                title_clean=normalize_title(item["title"]),
                abstract=item["abstract"],
                authors=item["authors"],
                first_author=extract_first_author(item["authors"]),
                year=item["year"],
                journal=item["journal"],
                volume=item["volume"],
                pages=item["pages"],
                language=item["language"],
                pub_types=item["pub_types"],
                mesh_terms=item["mesh_terms"],
                raw={"seeded": True},
                first_seen_run=run_fetch.id,
            )
            db.add(rec)
            await db.flush()
            record_ids.append(rec.id)

        # 4. Link records
        for rank, rid in enumerate(record_ids, start=1):
            stmt = insert_fn(QueryRecord).values(
                query_version_id=version.id, record_id=rid, rank_in_results=rank
            ).on_conflict_do_nothing(index_elements=["query_version_id", "record_id"])
            await db.execute(stmt)

        await db.commit()
        print(f"✓ Linked {len(record_ids)} records to version {version.id}")

        # 5. Dedup
        d_run = await create_dedup_run(db, [version.id], record_ids)
        d_stats = await execute_dedup_run(db, d_run.id)
        print(f"✓ dedup: {d_stats}")

        # 6. Screen
        s_run = await create_screening_run(db, version.id)
        s_stats = await execute_screening_run(db, s_run.id)
        print(f"✓ screen: {s_stats}")

        # 7. PICO
        p_run = await create_pico_run(db, version.id)
        p_stats = await execute_pico_run(db, p_run.id)
        print(f"✓ pico: {p_stats}")

        return version.id


if __name__ == "__main__":
    vid = asyncio.run(seed_second_topic())
    print(f"Second topic ready on query_version_id={vid}")
