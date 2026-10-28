from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class QueryVersion(Base):
    __tablename__ = "query_versions"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'approved', 'fetched', 'archived')", name="ck_query_versions_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("query_versions.id"), nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_struct: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    date_from: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="draft")
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class QueryHit(Base):
    __tablename__ = "query_hits"
    __table_args__ = (PrimaryKeyConstraint("query_version_id", "source"),)

    query_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("query_versions.id"), nullable=False, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False, primary_key=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RunLog(Base):
    __tablename__ = "run_log"
    __table_args__ = (
        CheckConstraint("step IN ('count', 'fetch', 'dedup', 'screen', 'pico', 'export')", name="ck_run_log_step"),
        CheckConstraint("status IN ('running', 'done', 'failed')", name="ck_run_log_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    query_version_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("query_versions.id"), nullable=True)
    step: Mapped[str] = mapped_column(String, nullable=False)
    rule_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        CheckConstraint("source IN ('pubmed', 'europepmc')", name="ck_records_source"),
        UniqueConstraint("source", "source_id", name="uq_records_source_source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    pmid: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_clean: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    first_author: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    journal: Mapped[str | None] = mapped_column(Text, nullable=True)
    volume: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    pub_types: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    mesh_terms: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    first_seen_run: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("run_log.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class QueryRecord(Base):
    __tablename__ = "query_records"
    __table_args__ = (PrimaryKeyConstraint("query_version_id", "record_id"),)

    query_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("query_versions.id"), nullable=False, primary_key=True)
    record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False, primary_key=True)
    rank_in_results: Mapped[int | None] = mapped_column(Integer, nullable=True)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DedupPair(Base):
    __tablename__ = "dedup_pairs"
    __table_args__ = (
        CheckConstraint("method IN ('doi', 'pmid', 'fuzzy')", name="ck_dedup_pairs_method"),
        CheckConstraint("machine_verdict IN ('duplicate', 'uncertain', 'distinct')", name="ck_dedup_pairs_machine_verdict"),
        CheckConstraint("human_verdict IN ('duplicate', 'distinct') OR human_verdict IS NULL", name="ck_dedup_pairs_human_verdict"),
        CheckConstraint("record_a_id < record_b_id", name="ck_dedup_pairs_order"),
        PrimaryKeyConstraint("record_a_id", "record_b_id"),
    )

    record_a_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False, primary_key=True)
    record_b_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False, primary_key=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str] = mapped_column(String, nullable=False)
    machine_verdict: Mapped[str] = mapped_column(String, nullable=False)
    human_verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    human_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("run_log.id"), nullable=True)


class DedupGroup(Base):
    __tablename__ = "dedup_groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    representative_record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("run_log.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (PrimaryKeyConstraint("group_id", "record_id"),)

    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("dedup_groups.id"), nullable=False, primary_key=True)
    record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False, primary_key=True)
    is_representative: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class Criteria(Base):
    __tablename__ = "criteria"
    __table_args__ = (
        CheckConstraint("kind IN ('include', 'exclude')", name="ck_criteria_kind"),
        CheckConstraint("source_type IN ('metadata', 'algorithm')", name="ck_criteria_source_type"),
        UniqueConstraint("code", name="uq_criteria_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class MachineScreening(Base):
    __tablename__ = "machine_screening"
    __table_args__ = (
        CheckConstraint("result IN ('hit', 'no_hit', 'unknown')", name="ck_machine_screening_result"),
        UniqueConstraint("record_id", "criterion_id", "rule_version", name="uq_machine_screening_record_criterion_rule"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False)
    criterion_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("criteria.id"), nullable=False)
    result: Mapped[str] = mapped_column(String, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("run_log.id"), nullable=True)


class MachineDecision(Base):
    __tablename__ = "machine_decisions"
    __table_args__ = (
        CheckConstraint("decision IN ('include', 'exclude', 'maybe')", name="ck_machine_decisions_decision"),
        PrimaryKeyConstraint("record_id", "rule_version"),
    )

    record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False, primary_key=True)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    reasons: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("run_log.id"), nullable=True)


class HumanDecision(Base):
    __tablename__ = "human_decisions"
    __table_args__ = (CheckConstraint("decision IN ('include', 'exclude', 'maybe')", name="ck_human_decisions_decision"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False)
    group_id_at_time: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("dedup_groups.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PicoMachine(Base):
    __tablename__ = "pico_machine"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    span_text: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("run_log.id"), nullable=True)


class PicoHuman(Base):
    __tablename__ = "pico_human"
    __table_args__ = (
        CheckConstraint("action IN ('add', 'remove', 'edit')", name="ck_pico_human_action"),
        CheckConstraint("label IN ('P', 'I', 'C', 'O') OR label IS NULL", name="ck_pico_human_label"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("records.id"), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    machine_span_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pico_machine.id"), nullable=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    before_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    query_version_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("query_versions.id"), nullable=True)


# Optional view mappings to match schema.sql for inspection and integrative use.
class LatestHumanDecision(Base):
    __tablename__ = "v_latest_human_decision"
    __table_args__ = {"info": {"is_view": True}}

    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GroupDecision(Base):
    __tablename__ = "v_group_decision"
    __table_args__ = {"info": {"is_view": True}}

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    representative_record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    n_human_values: Mapped[int] = mapped_column(Integer, nullable=False)
    human_decision: Mapped[str | None] = mapped_column(String, nullable=True)
