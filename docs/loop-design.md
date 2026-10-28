# Incremental review loop

## Implemented loop

Fetching is identifier-first. PubMed runs `esearch`, removes existing `(source, source_id)` values, then sends only new PMIDs to `efetch`. Europe PMC first requests a `lite` result containing identifiers, removes existing source identifiers, then requests `core` JSON only for new identifiers. Every identifier in the result is linked to the current query version through `query_records`, including records already stored. Fetch statistics are `found`, `new`, `already_stored`, and `linked`; `new_record_ids` is also retained in run stats for downstream incremental work.

A completed fetch starts deduplication, screening, and PICO in that order. The optional `query_version_ids` field on `POST /runs/fetch` defines the dedup union; it defaults to the fetched version. Dedup compares pairs only when at least one member is newly fetched, reuses all persistent `dedup_pairs`, and never overwrites `human_verdict`. It then materializes groups from all effective duplicate pairs in scope. Screening skips existing `(record, criterion, rule_version)` results. PICO skips records whose current rule version and abstract hash already have results.

Human decisions remain record-keyed and append-only. They therefore follow a record into later query versions. When a new duplicate edge joins records with differing latest human decisions, the effective group status is `conflict`. A previously excluded record added to a later version appears under `excluded_reappeared` in the version diff.

## Version APIs

- `GET /queries/{id}/lineage` returns the root-to-parent ancestor chain, current version, and immediate children.
- `GET /queries/{id}/snapshot` returns sorted record IDs, deterministic connected components as sorted member lists, effective status per component, and a SHA-256 signature of that canonical JSON.
- `GET /queries/{id}/diff?against=` compares canonical snapshots; `against` defaults to the parent. It reports added records with `previously_decided`, removed IDs, unchanged count, newly merged groups with prior materialized group IDs, new conflicts, excluded records that reappeared, and summary counts.
- `POST /queries/{id}/activate` appends a completed `run_log` marker with `step='export'`, `rule_version='activation-v1'`, and `params.action='activate'`. The active version is the version on the newest such marker. This avoids mutating immutable query versions and avoids a migration, at the cost of reusing the existing `export` step because `schema.sql` has no activation table or activation step.
- `GET /runs?query_version_id=&step=` lists run history newest first.

Viewing an older query version is the revert operation. It does not delete records, links, pairs, groups, machine outputs, decisions, or audit history. Its snapshot remains order-independent and stable unless a shared record receives a later human decision—which is expected because human decisions are global and record-keyed, not version snapshots.

## Differences from the requested design

There was no previous `docs/loop-design.md`; this file records the implemented design from the prompt.

- Europe PMC identifier discovery uses `resultType=lite`, then a batched `EXT_ID:` core query for unseen IDs.
- Activation uses an append-only `run_log` export marker. A dedicated active-version table would be clearer, but requires a migration and approval.
- Fetch launches downstream jobs sequentially inside one background task. Each downstream stage still receives its own `run_log` row, but there is no separate workflow/orchestration table.
- Snapshot group identities are member lists, not materialized group IDs, because materialized IDs change on rebuild. Diff includes old materialized IDs only as explanatory evidence for newly merged groups.

## Honest limitations

- Europe PMC ID batches are currently limited by the global `MAX_RECORDS` cap and are not cursor-paginated.
- PubMed and Europe PMC body requests are one batch each; very large configured limits need batching.
- A downstream failure marks its own run failed, but the successful fetch run stays done; there is no parent workflow status or automatic retry queue.
- Activation markers reuse `step='export'`; filtering export runs must inspect `params.action` to distinguish activations from CSV exports.
- Effective statuses in snapshots intentionally reflect the latest global record decision. They are not historical “decision as of version creation” snapshots.
