# Mini-Rayyan Backend

This backend implements the Hour-12 checkpoint for the Mini-Rayyan project:

- saved query versions with hit counts per source
- researcher approval flow
- incremental fetch from PubMed and Europe PMC into the Postgres schema
- asynchronous fetch runs with pollable status and idempotent persistence

## Prerequisites

- Python 3.11+
- PostgreSQL database (Supabase, pooler connection string)
- `.env` configured with `DATABASE_URL` and the NCBI settings

## Environment setup

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

Required variables:

```env
DATABASE_URL=postgresql+asyncpg://... 
NCBI_API_KEY=
NCBI_TOOL=mini-rayyan
NCBI_EMAIL=dev@example.com
USE_CACHE=true
MAX_RECORDS=250
```

Do not commit secrets. The `.env` file is gitignored.

## Install dependencies

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run the API

```bash
cd backend
. .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Example endpoints

- `GET /health`
- `POST /queries`
- `GET /queries`
- `GET /queries/{query_version_id}`
- `POST /queries/{query_version_id}/count`
- `POST /queries/{query_version_id}/approve`
- `POST /runs/fetch`
- `GET /runs/{run_id}`
- `GET /queries/{query_version_id}/records?limit=50&offset=0`
- `GET /records/{record_id}`
- `POST /dedup/run`
- `GET /dedup/groups?query_version_id=...`
- `GET /dedup/pairs?status=uncertain&query_version_id=...`
- `POST /dedup/pairs/{record_a_id}/{record_b_id}/verdict`
- `POST /screening/run`
- `GET /criteria`
- `PATCH /criteria/{criterion_id}`
- `GET /records/{record_id}/screening`
- `GET /screening/summary?query_version_id=...`
- `POST /pico/run`
- `GET /records/{record_id}/pico`
- `GET /review/records`
- `POST /records/{record_id}/decision`
- `POST /records/{record_id}/decision/undo`
- `POST /records/{record_id}/pico`
- `GET /records/{record_id}/history`
- `GET /audit`
- `GET /conflicts`
- `POST /groups/{group_id}/resolve`
- `GET /export/included.csv`

`POST /runs/fetch` accepts an approved `query_version_id`, creates a `run_log`
row, returns `202` and the run ID immediately, and completes the provider fetch
as a background task. Poll `GET /runs/{run_id}` for `running`, `done`, or
`failed`. `MAX_RECORDS` is the overall record cap for a run, across providers.

Example request:

```json
{"query_version_id": 42, "source": "pubmed"}
```

Omit `source` to fetch both PubMed and Europe PMC.

## Refresh real API fixtures

The parser tests use saved external response fixtures and never call live APIs:

```bash
cd backend
. .venv/bin/activate
python scripts/save_api_fixtures.py
```

The fixture provenance and the Europe PMC service-outage fallback are documented
in `tests/fixtures/README.md`.

## Deduplication

Deduplication runs asynchronously over the union of one or more query versions:

```json
{"query_version_ids": [42, 43]}
```

The algorithm, thresholds, human-review behavior, report command, and known
limits are documented in `docs/dedup.md`.

## Screening

Seed criteria once, then queue screening for a query version:

```bash
python scripts/seed_criteria.py
```

```json
{"query_version_id": 42}
```

Rules, independent versions, decision logic, configuration, and report usage
are documented in `docs/screening.md`.

## PICO tagging

PICO tagging runs offline on Include and Maybe abstracts and stores exact
character offsets. Rules, hash-based incremental behavior, overlap priority,
effective human corrections, evaluation, and limitations are documented in
`docs/pico.md`.

## Review and export

Shared effective status, append-only human writes, conflicts, history, audit,
and included CSV behavior are documented in `docs/review.md`.

## Tests

```bash
cd backend
. .venv/bin/activate
pytest -q
```
