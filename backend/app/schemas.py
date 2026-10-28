from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryVersionCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str | None = None
    query_text: str
    date_from: date | None = None
    date_to: date | None = None
    parent_id: int | None = None
    query_struct: dict[str, Any] | None = None
    created_by: str | None = None


class QueryVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None = None
    label: str | None = None
    query_text: str
    query_struct: dict[str, Any] | None = None
    date_from: date | None = None
    date_to: date | None = None
    status: str = "draft"
    created_by: str | None = None
    created_at: datetime


class QueryHitCreate(BaseModel):
    query_version_id: int
    source: str
    hit_count: int


class QueryHitRead(QueryHitCreate):
    model_config = ConfigDict(from_attributes=True)

    counted_at: datetime


class RunLogCreate(BaseModel):
    query_version_id: int | None = None
    step: str
    rule_version: str | None = None
    params: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    status: str = "running"


class RunLogRead(RunLogCreate):
    id: int
    started_at: datetime
    finished_at: datetime | None = None


class FetchRequest(BaseModel):
    query_version_id: int
    source: str | None = None
    query_version_ids: list[int] | None = None


class HealthResponse(BaseModel):
    status: str = "ok"


class QueryVersionList(BaseModel):
    versions: list[QueryVersionRead]
    hits: dict[str, int]


class QueryCountResponse(BaseModel):
    query_version_id: int
    hits: dict[str, int]


class FetchResponse(BaseModel):
    query_version_id: int
    source: str
    run_id: int
    status: str = "running"


class QueryLineageRead(BaseModel):
    current: QueryVersionRead
    ancestors: list[QueryVersionRead]
    children: list[QueryVersionRead]


class QuerySnapshotRead(BaseModel):
    query_version_id: int
    signature: str
    record_ids: list[int]
    groups: list[list[int]]
    effective_statuses: list[dict[str, Any]]


class QueryDiffRead(BaseModel):
    query_version_id: int
    against: int
    added: list[dict[str, Any]]
    removed: list[int]
    unchanged_count: int
    groups_newly_merged: list[dict[str, Any]]
    new_conflicts: list[list[int]]
    excluded_reappeared: list[int]
    summary: dict[str, int]


class QueryActivationRead(BaseModel):
    active_query_version_id: int
    run_id: int


class RecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_id: str
    pmid: str | None = None
    doi: str | None = None
    title: str | None = None
    title_clean: str | None = None
    abstract: str | None = None
    authors: list[str] | None = None
    first_author: str | None = None
    year: int | None = None
    journal: str | None = None
    volume: str | None = None
    pages: str | None = None
    language: str | None = None
    pub_types: list[str] | None = None
    mesh_terms: list[Any] | None = None
    raw: dict[str, Any] | None = None
    first_seen_run: int | None = None
    created_at: datetime


class RecordPage(BaseModel):
    items: list[RecordRead]
    limit: int
    offset: int
    total: int


class DedupRunRequest(BaseModel):
    query_version_ids: list[int] = Field(min_length=1)


class DedupRunAccepted(BaseModel):
    run_id: int
    query_version_ids: list[int]
    status: str = "running"


class DedupRecordSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_id: str
    pmid: str | None = None
    doi: str | None = None
    title: str | None = None
    abstract: str | None = None
    first_author: str | None = None
    year: int | None = None
    journal: str | None = None
    pages: str | None = None
    pub_types: list[str] | None = None


class DedupPairRead(BaseModel):
    record_a: DedupRecordSummary
    record_b: DedupRecordSummary
    method: str
    score: float | None = None
    machine_verdict: str
    human_verdict: str | None = None
    effective_verdict: str
    human_by: str | None = None
    human_at: datetime | None = None


class DedupPairPage(BaseModel):
    items: list[DedupPairRead]
    limit: int
    offset: int
    total: int


class DedupGroupMemberRead(BaseModel):
    record: DedupRecordSummary
    is_representative: bool


class DedupPairEvidence(BaseModel):
    record_a_id: int
    record_b_id: int
    method: str
    score: float | None = None
    effective_verdict: str


class DedupGroupRead(BaseModel):
    id: int
    run_id: int
    representative_record_id: int
    members: list[DedupGroupMemberRead]
    pair_evidence: list[DedupPairEvidence]


class DedupGroupPage(BaseModel):
    items: list[DedupGroupRead]
    limit: int
    offset: int
    total: int


class DedupVerdictRequest(BaseModel):
    verdict: Literal["duplicate", "distinct"] | None
    reviewer: str = Field(min_length=1)


class DedupVerdictResponse(BaseModel):
    run_id: int
    groups: list[DedupGroupRead]


class ScreeningRunRequest(BaseModel):
    query_version_id: int


class ScreeningRunAccepted(BaseModel):
    run_id: int
    query_version_id: int
    status: str = "running"


class CriterionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    label: str
    kind: str
    reason: str
    source_type: str
    active: bool


class CriterionPatch(BaseModel):
    active: bool
    reviewer: str = Field(default="reviewer", min_length=1)


class CriterionScreeningRead(BaseModel):
    criterion: CriterionRead
    result: str
    evidence: str | None = None
    rule_version: str
    run_id: int | None = None


class MachineDecisionRead(BaseModel):
    decision: str
    reasons: list[str]
    rule_version: str
    run_id: int | None = None


class RecordScreeningRead(BaseModel):
    record_id: int
    criteria: list[CriterionScreeningRead]
    machine_decision: MachineDecisionRead


class ScreeningSummary(BaseModel):
    records: int
    include: int
    exclude: int
    maybe: int
    by_reason: dict[str, int]


class CriterionPatchResponse(BaseModel):
    criterion: CriterionRead
    recomputed_runs: list[int]
    counts: ScreeningSummary


class PicoRunRequest(BaseModel):
    query_version_id: int


class PicoRunAccepted(BaseModel):
    run_id: int
    query_version_id: int
    status: str = "running"


class EffectivePicoSpanRead(BaseModel):
    id: int
    source: Literal["machine", "human"]
    label: Literal["P", "I", "C", "O"]
    start_char: int
    end_char: int
    span_text: str
    method: str
    confidence: float | None = None
    rule_version: str | None = None


class RecordPicoRead(BaseModel):
    record_id: int
    spans: list[EffectivePicoSpanRead]


class EffectiveStatusRead(BaseModel):
    status: Literal["include", "exclude", "maybe", "conflict"]
    decided_by: Literal["machine", "human"]
    reasons: list[str]
    note: str | None = None
    reviewer: str | None = None
    decided_at: datetime | None = None
    representative_record_id: int
    group_id: int | None = None


class ReviewRecordRead(BaseModel):
    record: DedupRecordSummary
    effective: EffectiveStatusRead
    machine_decision: str | None = None
    machine_reasons: list[str]
    group_size: int
    hidden: bool
    pico_spans: list[EffectivePicoSpanRead]


class ReviewRecordPage(BaseModel):
    items: list[ReviewRecordRead]
    counts: dict[str, int]
    limit: int
    offset: int
    total: int


class HumanDecisionRequest(BaseModel):
    decision: Literal["include", "exclude", "maybe"]
    reason: str | None = None
    note: str | None = None
    reviewer: str = Field(min_length=1)


class UndoDecisionRequest(BaseModel):
    reviewer: str = Field(min_length=1)


class PicoHumanRequest(BaseModel):
    action: Literal["add", "remove", "edit"]
    label: Literal["P", "I", "C", "O"] | None = None
    start_char: int | None = None
    end_char: int | None = None
    machine_span_id: int | None = None
    reviewer: str = Field(min_length=1)


class HistoryEventRead(BaseModel):
    timestamp: datetime
    event_type: str
    source: Literal["machine", "human"]
    data: dict[str, Any]


class RecordHistoryRead(BaseModel):
    record_id: int
    events: list[HistoryEventRead]


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before_value: dict[str, Any] | None = None
    after_value: dict[str, Any] | None = None
    query_version_id: int | None = None


class AuditPage(BaseModel):
    items: list[AuditRead]
    limit: int
    offset: int
    total: int


class ConflictMemberRead(BaseModel):
    record: DedupRecordSummary
    latest_decision: str | None = None
    reviewer: str | None = None
    decided_at: datetime | None = None


class ConflictRead(BaseModel):
    group_id: int
    representative_record_id: int
    members: list[ConflictMemberRead]


class GroupResolveResponse(BaseModel):
    group_id: int
    effective: EffectiveStatusRead
