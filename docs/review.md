# Review layer

## Effective status

`review_service.effective_status` is the single definition used by review API
rows and CSV export:

1. Resolve a hidden duplicate through its current dedup group representative.
2. Read each member's latest append-only human decision.
3. Different member decisions produce `conflict`.
4. One shared human value wins over machine screening.
5. With no human value, use the representative's current machine decision.

The result includes status, machine/human provenance, reasons, note, reviewer,
time, representative ID, and group ID.

## Writes and audit

Record decisions, group resolutions, and PICO corrections require a reviewer.
Each human row and its `audit_log` before/after row commit together. Decisions
and PICO corrections are append-only. A group resolution inserts the selected
decision for every member so the conflict disappears without silently choosing
one member.

Undo inserts the previous human decision as a new row. Restoring machine
control after the first human decision cannot be represented by the current
non-null `human_decisions.decision` column. Migration
`db/migrations/001_human_decision_restore_machine.sql` is proposed but remains
unapplied pending approval.

## Review list and conflicts

Review rows use representatives by default. `include_hidden=true` exposes all
members while retaining their group-effective decision. Filtering supports
status, machine/human provenance, reason, source, and text/identifier search.
Counts are calculated before the status filter so they can power status tabs.

Conflict listing uses the latest materialized dedup run that contains the query
version. Records not yet covered by a dedup run are treated as singleton groups.

## Effective PICO

The review API and export both use `merge_effective_spans`: current
hash-matched machine spans, minus latest human removals, with human additions
and edits. Added/edited offsets must exactly reproduce text from the abstract.

## CSV export

Included export emits one row per effectively included representative. It is
UTF-8 with BOM and uses Python CSV quoting. PICO values are joined with ` | `;
query-version provenance is unioned across group members; non-representative
source IDs are retained in `other_source_ids`.
