"""Canonical Step 0 criteria seed definitions."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Criteria


def criterion_seed_rows() -> list[dict[str, str | bool]]:
    """Return deterministic, idempotently seedable criterion rows."""

    return [
        {"code": "non_human", "label": "Animal-only study", "kind": "exclude", "reason": "The review question is about people.", "source_type": "metadata", "active": True},
        {"code": "non_english", "label": "Non-English text", "kind": "exclude", "reason": "The review team cannot reliably assess non-English text.", "source_type": "metadata", "active": True},
        {"code": "simulation", "label": "Simulation or modelling study", "kind": "exclude", "reason": "The study models outcomes rather than measuring patients.", "source_type": "algorithm", "active": True},
        {"code": "case_report", "label": "Case report", "kind": "exclude", "reason": "One patient cannot estimate an average treatment effect.", "source_type": "metadata", "active": True},
        {"code": "pediatric_only", "label": "Pediatric-only study", "kind": "exclude", "reason": "The review population is adults.", "source_type": "metadata", "active": True},
        {"code": "non_primary", "label": "Non-primary publication", "kind": "exclude", "reason": "The publication does not report eligible primary measurements.", "source_type": "metadata", "active": True},
        {"code": "human_adult", "label": "Human adults", "kind": "include", "reason": "The review population is adults with human participants.", "source_type": "metadata", "active": True},
        {"code": "trial_design", "label": "Comparative trial design", "kind": "include", "reason": "Comparative trials can answer the review question.", "source_type": "algorithm", "active": True},
        {"code": "topic_match", "label": "GLP-1 intervention mentioned", "kind": "include", "reason": "The record must address a configured intervention from the review question.", "source_type": "algorithm", "active": True},
    ]


async def seed_criteria(db) -> int:
    """Idempotently insert/update definitions without resetting active choices."""

    insert_fn = sqlite_insert if db.bind.dialect.name == "sqlite" else postgres_insert
    for row in criterion_seed_rows():
        statement = insert_fn(Criteria).values(**row)
        await db.execute(statement.on_conflict_do_update(
            index_elements=["code"],
            set_={
                "label": statement.excluded.label,
                "kind": statement.excluded.kind,
                "reason": statement.excluded.reason,
                "source_type": statement.excluded.source_type,
            },
        ))
    await db.commit()
    return len(criterion_seed_rows())
