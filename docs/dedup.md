# Step 4: Deduplication

## Purpose and scope

Deduplication groups database records that describe the same paper. It does not
delete records. Every record in scope is materialized into exactly one group for
each dedup run; one member is visible as the representative and the other
members are hidden by `group_members.is_representative = false`.

A run accepts one or more `query_version_ids`. Its record set is the union of
their `query_records`. The sorted version IDs and all rule parameters are saved
in `run_log.params`; `run_log.query_version_id` is the lowest version ID. This
uses the existing schema and keeps every machine output tied to a dedup run.

Further reports of the same study, such as a conference abstract and a later
journal paper, are not automatically merged.

## Algorithm

### 1. Exact baseline

Two records are duplicates when they share a non-empty DOI or PMID. DOI takes
precedence if both identifiers match. PMID matching intentionally crosses data
sources because a Europe PMC record can carry the PMID of its PubMed record.

The exact-only baseline in the report is the number of effective duplicate
pairs whose method is `doi` or `pmid`.

### 2. Blocking

Records not already paired by an exact identifier are compared only when they:

- have the same cleaned first author and publication years within plus/minus one;
  or
- have the same first 30 characters of `title_clean`.

This avoids an all-pairs comparison while retaining likely matches.

### 3. Fuzzy scoring

RapidFuzz `token_sort_ratio` supplies title and journal similarity. The score is
bounded to 0..1 and uses:

| Field | Weight | Match method |
| --- | ---: | --- |
| `title_clean` | 0.82 | token-sort similarity |
| `year` | 0.06 | exact equality |
| `first_author` | 0.06 | case-insensitive equality |
| `journal` | 0.03 | token-sort similarity |
| `pages` | 0.03 | case-insensitive equality |

With these weights, an exact title, year, and first author scores 0.94 without
requiring journal or page metadata.

- Score at least `DUP_HIGH=0.93`, with equal non-null year and first author:
  `duplicate`.
- Score at least `DUP_LOW=0.85` but not eligible for automatic duplication:
  `uncertain`.
- Lower scores are not persisted.

All weights and thresholds are centralized in `app/config.py` and copied into
the run parameters.

### 4. Companion-report guard

A fuzzy duplicate is downgraded to `uncertain` when either:

- title similarity is at least 0.90 and exactly one record has a publication
  type containing `Congress`, `Meeting Abstract`, `Conference`, or `Abstract`;
  or
- both abstracts have at least 100 characters and their token-set similarity is
  below 0.55.

Exact DOI/PMID matches remain exact duplicates. Uncertain pairs do not merge
without a human `duplicate` verdict.

### 5. Effective verdict and groups

The effective verdict is `COALESCE(human_verdict, machine_verdict)`. Union-find
builds connected components from effective duplicate edges. A human `distinct`
therefore breaks that edge on every later run, while a human `duplicate` turns
an uncertain edge into a merge. Persistent pair upserts update only machine
fields and never overwrite the human fields.

Singletons are materialized so every in-scope record belongs to a group.

### 6. Representative

The representative is selected deterministically in this order:

1. has a DOI;
2. has the longer abstract;
3. source is PubMed;
4. lowest record ID.

Exactly one group member is marked representative.

## Human review and audit

Setting or clearing a human pair verdict writes `audit_log` with the before and
after human state. The verdict, audit row, new dedup run, and rebuilt groups are
committed in one transaction. Historical groups are retained.

## Reporting

Run:

```bash
cd backend
python scripts/dedup_report.py --query-version-id 1
```

The output separates effective exact-only duplicate pairs, additional fuzzy
duplicate pairs, their combined total, and uncertain pairs. `--run-id` can be
used instead; with no argument the latest completed dedup run is reported.

## Known limits and likely failures

- Missing or incorrectly normalized authors, titles, or years can prevent a
  true match from entering a block.
- Common title prefixes can create large blocks and false candidates.
- Translated titles and heavily abbreviated titles may score too low.
- Page numbering and journal naming vary across providers and contribute only
  small boosts for this reason.
- Abstract similarity is a safety guard, not proof that two reports are the
  same or different study.
- Shared DOI/PMID metadata is treated as authoritative; a provider metadata
  error can therefore create a false exact match.
- Connected components are transitive: if A=B and B=C, all three group even
  when A and C have no direct duplicate edge. Reviewers can mark an incorrect
  bridge pair `distinct` and rebuild the groups.
