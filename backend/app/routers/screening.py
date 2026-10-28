"""Screening runs, criteria management, evidence, and summaries."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import Criteria, MachineDecision, MachineScreening, Record
from app.schemas import (
    CriterionPatch,
    CriterionPatchResponse,
    CriterionRead,
    CriterionScreeningRead,
    MachineDecisionRead,
    RecordScreeningRead,
    ScreeningRunAccepted,
    ScreeningRunRequest,
    ScreeningSummary,
)
from app.services.screening_rules import rule_versions
from app.services.screening_service import (
    create_screening_run,
    run_screening_task,
    screening_summary,
    toggle_criterion,
)


router = APIRouter(tags=["screening"])


@router.post("/screening/run", response_model=ScreeningRunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_screening_run(
    payload: ScreeningRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ScreeningRunAccepted:
    """Queue an incremental screening run for every record in a query version."""

    run = await create_screening_run(db, payload.query_version_id)
    background_tasks.add_task(run_screening_task, run.id)
    return ScreeningRunAccepted(run_id=run.id, query_version_id=payload.query_version_id)


@router.get("/criteria", response_model=list[CriterionRead])
async def list_criteria(db: AsyncSession = Depends(get_db)) -> list[CriterionRead]:
    """List all criteria, including inactive ones."""

    return list((await db.scalars(select(Criteria).order_by(Criteria.id))).all())


@router.patch("/criteria/{criterion_id}", response_model=CriterionPatchResponse)
async def patch_criterion(
    criterion_id: int,
    payload: CriterionPatch,
    db: AsyncSession = Depends(get_db),
) -> CriterionPatchResponse:
    """Toggle a criterion, audit it, and recompute decisions without rerunning rules."""

    criterion, run_ids, counts = await toggle_criterion(
        db, criterion_id, payload.active, payload.reviewer
    )
    return CriterionPatchResponse(criterion=criterion, recomputed_runs=run_ids, counts=counts)


@router.get("/records/{record_id}/screening", response_model=RecordScreeningRead)
async def get_record_screening(
    record_id: int, db: AsyncSession = Depends(get_db)
) -> RecordScreeningRead:
    """Return current per-rule evidence and the combined machine decision."""

    if await db.get(Record, record_id) is None:
        raise HTTPException(status_code=404, detail="Record not found")
    criteria = list((await db.scalars(select(Criteria).order_by(Criteria.id))).all())
    versions = rule_versions()
    rows = list(
        (
            await db.scalars(
                select(MachineScreening).where(MachineScreening.record_id == record_id)
            )
        ).all()
    )
    by_criterion = {
        row.criterion_id: row
        for row in rows
        if row.criterion_id in {criterion.id for criterion in criteria}
        and row.rule_version == versions[next(criterion.code for criterion in criteria if criterion.id == row.criterion_id)]
    }
    decision = await db.get(MachineDecision, (record_id, settings.SCREEN_DECISION_VERSION))
    if decision is None:
        raise HTTPException(status_code=404, detail="Record has not been screened")
    results = [
        CriterionScreeningRead(
            criterion=criterion,
            result=by_criterion[criterion.id].result,
            evidence=by_criterion[criterion.id].evidence,
            rule_version=by_criterion[criterion.id].rule_version,
            run_id=by_criterion[criterion.id].run_id,
        )
        for criterion in criteria
        if criterion.code in versions and criterion.id in by_criterion
    ]
    return RecordScreeningRead(
        record_id=record_id,
        criteria=results,
        machine_decision=MachineDecisionRead(
            decision=decision.decision,
            reasons=decision.reasons or [],
            rule_version=decision.rule_version,
            run_id=decision.run_id,
        ),
    )


@router.get("/screening/summary", response_model=ScreeningSummary)
async def get_screening_summary(
    query_version_id: int, db: AsyncSession = Depends(get_db)
) -> ScreeningSummary:
    """Return decision totals and exclusion-reason totals for a query version."""

    return ScreeningSummary(**await screening_summary(db, query_version_id))
