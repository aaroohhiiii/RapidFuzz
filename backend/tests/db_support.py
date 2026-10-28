"""Disposable SQLite schema used by tests; never connects to configured data."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


TEST_DDL = (
    """CREATE TABLE query_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, label TEXT,
        query_text TEXT NOT NULL, query_struct JSON, date_from DATE, date_to DATE,
        status TEXT NOT NULL DEFAULT 'draft', created_by TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE run_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, query_version_id INTEGER,
        step TEXT NOT NULL, rule_version TEXT, params JSON, stats JSON,
        status TEXT NOT NULL DEFAULT 'running',
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TIMESTAMP
    )""",
    """CREATE TABLE records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, source_id TEXT NOT NULL,
        pmid TEXT, doi TEXT, title TEXT, title_clean TEXT, abstract TEXT, authors JSON,
        first_author TEXT, year INTEGER, journal TEXT, volume TEXT, pages TEXT,
        language TEXT, pub_types JSON, mesh_terms JSON, raw JSON, first_seen_run INTEGER,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (source, source_id)
    )""",
    """CREATE TABLE query_records (
        query_version_id INTEGER NOT NULL, record_id INTEGER NOT NULL,
        rank_in_results INTEGER, found_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (query_version_id, record_id)
    )""",
    """CREATE TABLE dedup_pairs (
        record_a_id INTEGER NOT NULL, record_b_id INTEGER NOT NULL, score REAL,
        method TEXT NOT NULL, machine_verdict TEXT NOT NULL, human_verdict TEXT,
        human_by TEXT, human_at TIMESTAMP, run_id INTEGER,
        PRIMARY KEY (record_a_id, record_b_id), CHECK (record_a_id < record_b_id)
    )""",
    """CREATE TABLE dedup_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, representative_record_id INTEGER NOT NULL,
        run_id INTEGER, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE group_members (
        group_id INTEGER NOT NULL, record_id INTEGER NOT NULL,
        is_representative BOOLEAN NOT NULL DEFAULT 0,
        PRIMARY KEY (group_id, record_id)
    )""",
    """CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actor TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL, before_value JSON, after_value JSON,
        query_version_id INTEGER
    )""",
    """CREATE TABLE criteria (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL,
        source_type TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE machine_screening (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL,
        criterion_id INTEGER NOT NULL, result TEXT NOT NULL, evidence TEXT,
        rule_version TEXT NOT NULL, run_id INTEGER,
        UNIQUE (record_id, criterion_id, rule_version)
    )""",
    """CREATE TABLE machine_decisions (
        record_id INTEGER NOT NULL, rule_version TEXT NOT NULL,
        decision TEXT NOT NULL, reasons JSON, run_id INTEGER,
        PRIMARY KEY (record_id, rule_version)
    )""",
    """CREATE TABLE pico_machine (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL,
        label TEXT NOT NULL, start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
        span_text TEXT NOT NULL, method TEXT, confidence REAL,
        rule_version TEXT NOT NULL, run_id INTEGER
    )""",
    """CREATE TABLE pico_human (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL,
        action TEXT NOT NULL, machine_span_id INTEGER, label TEXT,
        start_char INTEGER, end_char INTEGER, span_text TEXT,
        reviewer TEXT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE human_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL,
        group_id_at_time INTEGER, decision TEXT NOT NULL, reason TEXT, note TEXT,
        reviewer TEXT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
)


async def create_test_database(path: Path):
    """Create an isolated SQLite database from explicit test DDL."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for statement in TEST_DDL:
            await connection.exec_driver_sql(statement)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
