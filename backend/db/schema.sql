-- Mini-Rayyan schema (PostgreSQL / Supabase)
-- Rules baked in:
--   1. Nothing is deleted: duplicates/exclusions are flags, never removed rows.
--   2. Every machine output points to a run (run_log), which points to a query version.
--   3. Machine results and human decisions live in separate tables. Human always wins.
--   4. Human decisions are append-only and keyed to a RECORD, so group merges never lose them.
--   5. Every human change is written to audit_log.

-- ============================================================
-- 1. QUERIES
-- ============================================================
CREATE TABLE query_versions (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  parent_id        BIGINT REFERENCES query_versions(id),   -- version this was edited from
  label            TEXT,                                    -- e.g. "semaglutide + placebo"
  query_text       TEXT NOT NULL,                           -- human-readable boolean string
  query_struct     JSONB,                                   -- parsed terms / operators
  date_from        DATE,
  date_to          DATE,
  status           TEXT NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft','approved','fetched','archived')),
  created_by       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- hit count per source for each version (shown before the researcher approves)
CREATE TABLE query_hits (
  query_version_id BIGINT NOT NULL REFERENCES query_versions(id),
  source           TEXT NOT NULL CHECK (source IN ('pubmed','europepmc')),
  hit_count        INTEGER NOT NULL,
  counted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (query_version_id, source)
);

-- ============================================================
-- 2. RUN LOG (declared early: other tables reference it)
-- ============================================================
CREATE TABLE run_log (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  query_version_id BIGINT REFERENCES query_versions(id),
  step             TEXT NOT NULL
                   CHECK (step IN ('count','fetch','dedup','screen','pico','export')),
  rule_version     TEXT,                                    -- version of the rules/algorithm used
  params           JSONB,
  stats            JSONB,                                   -- e.g. {"new":120,"dupes":14}
  status           TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running','done','failed')),
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at      TIMESTAMPTZ
);

-- ============================================================
-- 3. RECORDS (one row per source record; never deleted)
-- ============================================================
CREATE TABLE records (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source           TEXT NOT NULL CHECK (source IN ('pubmed','europepmc')),
  source_id        TEXT NOT NULL,                           -- PMID for pubmed, EPMC id for europepmc
  pmid             TEXT,
  doi              TEXT,                                    -- stored lowercased, trimmed
  title            TEXT,
  title_clean      TEXT,                                    -- lowercase, no punctuation (for dedup)
  abstract         TEXT,
  authors          JSONB,                                   -- ["Smith J","Lee K"]
  first_author     TEXT,                                    -- cleaned surname (for dedup)
  year             INTEGER,
  journal          TEXT,
  volume           TEXT,
  pages            TEXT,
  language         TEXT,                                    -- ISO-ish, e.g. 'eng'
  pub_types        JSONB,                                   -- ["Journal Article","Case Reports"]
  mesh_terms       JSONB,
  raw              JSONB,                                   -- original API payload
  first_seen_run   BIGINT REFERENCES run_log(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, source_id)
);
CREATE INDEX idx_records_doi        ON records (doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_records_pmid       ON records (pmid) WHERE pmid IS NOT NULL;
CREATE INDEX idx_records_title      ON records (title_clean);
CREATE INDEX idx_records_year_auth  ON records (year, first_author);

-- which query versions found which record (a record found twice is stored once)
CREATE TABLE query_records (
  query_version_id BIGINT NOT NULL REFERENCES query_versions(id),
  record_id        BIGINT NOT NULL REFERENCES records(id),
  rank_in_results  INTEGER,
  found_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (query_version_id, record_id)
);
CREATE INDEX idx_qr_record ON query_records (record_id);

-- ============================================================
-- 4. DEDUPLICATION
-- ============================================================
-- Persistent candidate pairs: machine verdict + optional human verdict.
-- Human "not a duplicate" survives every rerun.
CREATE TABLE dedup_pairs (
  record_a_id      BIGINT NOT NULL REFERENCES records(id),
  record_b_id      BIGINT NOT NULL REFERENCES records(id),
  score            REAL,                                    -- 0..1 similarity
  method           TEXT NOT NULL
                   CHECK (method IN ('doi','pmid','fuzzy')),
  machine_verdict  TEXT NOT NULL
                   CHECK (machine_verdict IN ('duplicate','uncertain','distinct')),
  human_verdict    TEXT CHECK (human_verdict IN ('duplicate','distinct')),
  human_by         TEXT,
  human_at         TIMESTAMPTZ,
  run_id           BIGINT REFERENCES run_log(id),
  PRIMARY KEY (record_a_id, record_b_id),
  CHECK (record_a_id < record_b_id)                         -- store each pair once
);

-- Materialized groups = connected components of pairs where
-- COALESCE(human_verdict, machine_verdict) = 'duplicate'. Rebuilt on each dedup run.
CREATE TABLE dedup_groups (
  id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  representative_record_id BIGINT NOT NULL REFERENCES records(id),
  run_id                   BIGINT REFERENCES run_log(id),
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
  group_id         BIGINT NOT NULL REFERENCES dedup_groups(id),
  record_id        BIGINT NOT NULL REFERENCES records(id),
  is_representative BOOLEAN NOT NULL DEFAULT FALSE,         -- FALSE = hidden, never deleted
  PRIMARY KEY (group_id, record_id)
);
CREATE INDEX idx_gm_record ON group_members (record_id);

-- ============================================================
-- 5. SCREENING (machine side)
-- ============================================================
CREATE TABLE criteria (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code             TEXT NOT NULL UNIQUE,                    -- 'non_human','non_english','simulation',...
  label            TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('include','exclude')),
  reason           TEXT NOT NULL,                           -- the one-line reason from Step 0
  source_type      TEXT NOT NULL CHECK (source_type IN ('metadata','algorithm')),
  active           BOOLEAN NOT NULL DEFAULT TRUE            -- reviewer toggles this
);

-- one row per record per criterion per rule version
CREATE TABLE machine_screening (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  record_id        BIGINT NOT NULL REFERENCES records(id),
  criterion_id     BIGINT NOT NULL REFERENCES criteria(id),
  result           TEXT NOT NULL CHECK (result IN ('hit','no_hit','unknown')),
  evidence         TEXT,                                    -- matched field/keyword, for transparency
  rule_version     TEXT NOT NULL,
  run_id           BIGINT REFERENCES run_log(id),
  UNIQUE (record_id, criterion_id, rule_version)
);

-- combined machine verdict per record (include / exclude / maybe) with reasons
CREATE TABLE machine_decisions (
  record_id        BIGINT NOT NULL REFERENCES records(id),
  rule_version     TEXT NOT NULL,
  decision         TEXT NOT NULL CHECK (decision IN ('include','exclude','maybe')),
  reasons          JSONB,                                   -- ["non_human","non_english"]
  run_id           BIGINT REFERENCES run_log(id),
  PRIMARY KEY (record_id, rule_version)
);

-- ============================================================
-- 6. HUMAN DECISIONS (append-only, keyed to a record)
-- ============================================================
CREATE TABLE human_decisions (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  record_id        BIGINT NOT NULL REFERENCES records(id),
  group_id_at_time BIGINT REFERENCES dedup_groups(id),      -- snapshot, for traceability only
  decision         TEXT NOT NULL CHECK (decision IN ('include','exclude','maybe')),
  reason           TEXT,
  note             TEXT,
  reviewer         TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_hd_record ON human_decisions (record_id, created_at DESC);

-- latest human decision per record
CREATE VIEW v_latest_human_decision AS
SELECT DISTINCT ON (record_id) record_id, decision, reason, note, reviewer, created_at
FROM human_decisions
ORDER BY record_id, created_at DESC, id DESC;

-- effective decision per group: human beats machine; differing human decisions = conflict
CREATE VIEW v_group_decision AS
SELECT
  g.id AS group_id,
  g.representative_record_id,
  COUNT(DISTINCT h.decision)                           AS n_human_values,
  CASE
    WHEN COUNT(DISTINCT h.decision) > 1 THEN 'conflict'          -- show to reviewer, never pick silently
    WHEN COUNT(DISTINCT h.decision) = 1 THEN MIN(h.decision)
    ELSE NULL                                                   -- no human decision: fall back to machine
  END                                                  AS human_decision
FROM dedup_groups g
JOIN group_members gm ON gm.group_id = g.id
LEFT JOIN v_latest_human_decision h ON h.record_id = gm.record_id
GROUP BY g.id, g.representative_record_id;

-- ============================================================
-- 7. PICO
-- ============================================================
CREATE TABLE pico_machine (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  record_id        BIGINT NOT NULL REFERENCES records(id),
  label            TEXT NOT NULL CHECK (label IN ('P','I','C','O')),
  start_char       INTEGER NOT NULL,                        -- offsets into records.abstract
  end_char         INTEGER NOT NULL,
  span_text        TEXT NOT NULL,
  method           TEXT,                                    -- 'regex','spacy','llm',...
  confidence       REAL,
  rule_version     TEXT NOT NULL,
  run_id           BIGINT REFERENCES run_log(id)
);
CREATE INDEX idx_pm_record ON pico_machine (record_id);

-- reviewer corrections: add a span, or remove a machine span
CREATE TABLE pico_human (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  record_id        BIGINT NOT NULL REFERENCES records(id),
  action           TEXT NOT NULL CHECK (action IN ('add','remove','edit')),
  machine_span_id  BIGINT REFERENCES pico_machine(id),      -- set for remove/edit
  label            TEXT CHECK (label IN ('P','I','C','O')),
  start_char       INTEGER,
  end_char         INTEGER,
  span_text        TEXT,
  reviewer         TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ph_record ON pico_human (record_id);

-- ============================================================
-- 8. AUDIT LOG (every human change)
-- ============================================================
CREATE TABLE audit_log (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor            TEXT NOT NULL,
  action           TEXT NOT NULL,        -- 'decision','dedup_override','pico_edit','criterion_toggle',...
  entity_type      TEXT NOT NULL,        -- 'record','dedup_pair','pico_span','criterion'
  entity_id        TEXT NOT NULL,
  before_value     JSONB,
  after_value      JSONB,
  query_version_id BIGINT REFERENCES query_versions(id)
);
CREATE INDEX idx_audit_entity ON audit_log (entity_type, entity_id);