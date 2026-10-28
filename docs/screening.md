# Step 5: Machine screening

## Scope and provenance

Screening evaluates every record linked to a query version, including hidden
deduplication members. Each criterion result is stored once per
`(record_id, criterion_id, rule_version)` in `machine_screening`, and each
combined decision is stored in `machine_decisions`. Both point to a `run_log`
row whose step is `screen` and whose `query_version_id` records the scope.

The criteria are seeded idempotently with:

```bash
cd backend
python scripts/seed_criteria.py
```

Re-seeding refreshes labels, reasons, kinds, and source types but deliberately
does not reset reviewer-selected `active` flags.

## Difference from Step 0

Step 0 lists “Reviews and editorials” together as exclusions. Step 5 narrows
automatic non-primary exclusions to Editorial, Letter, Comment, News, and
Published Erratum. Reviews and meta-analyses default to **Maybe**, because they
may be useful for citation chasing but do not themselves supply eligible
primary measurements. Set `SCREEN_REVIEWS_AS_EXCLUDE=true` to exclude them.

`topic_match` is additional to the Step 0 criteria but comes directly from the
review question and its intervention keyphrases.

## Criteria and exact rules

### Exclusion criteria

| Code | Primary source | Fields and rule |
| --- | --- | --- |
| `non_human` | Metadata, with keyword fallback | `mesh_terms` contains Animals and not Humans. When MeSH is absent, title/abstract contains mice, mouse, murine, rat, rats, rodent, in vitro, zebrafish, cell line, or preclinical with no patient/participant/adult/volunteer cue. |
| `non_english` | Metadata, with detector fallback | A present `language` other than en/eng/English is a hit. If missing, deterministic `langdetect` runs when title+abstract has at least 50 characters. |
| `simulation` | Own algorithm | Title/abstract contains simulation, simulated, in silico, Markov model, microsimulation, cost-effectiveness model, modelling/modeling study, or Monte Carlo. “Simulated patient” is guarded when education/training/student/standardized-patient wording is present. |
| `case_report` | Metadata, with title fallback | `pub_types` contains Case Report(s), or title contains “a case of”, “case report”, or “we report a patient”. |
| `pediatric_only` | Metadata, with keyword fallback | MeSH contains Child, Infant, or Adolescent without Adult. Otherwise pediatric/paediatric, child, adolescent, infant, or teenager cues with no adult cue. |
| `non_primary` | Metadata | `pub_types` contains Editorial, Letter, Comment, News, or Published Erratum. Review/meta-analysis yields review-specific unknown evidence and therefore Maybe by default, or a hit when configured for exclusion. |

The three metadata criteria with fallbacks retain `source_type=metadata` because
the structured field is authoritative and the algorithm is used only when the
field cannot answer the question.

### Positive inclusion evidence

| Code | Primary source | Fields and rule |
| --- | --- | --- |
| `human_adult` | Metadata | MeSH contains both Humans and Adult(s), or the abstract contains an adult cue. |
| `trial_design` | Own algorithm | `pub_types` contains Randomized Controlled Trial or Clinical Trial, or title/abstract contains randomized, randomised, placebo, double-blind, versus, or compared with. |
| `topic_match` | Own algorithm | Title/abstract contains semaglutide, liraglutide, tirzepatide, GLP-1, or glucagon-like peptide-1 spelling variants. |

All keyword lists, language length, review behavior, and independent rule
versions are centralized in `app/config.py`.

## Decision logic

Only active criteria affect the combined result; inactive results remain stored.

1. Any active exclusion hit produces **Exclude**. `reasons` contains every hit
   exclusion code.
2. Otherwise, all active positive criteria must be hits and an abstract must be
   present to produce **Include**.
3. Everything else is **Maybe**. Reasons identify unconfirmed positive criteria,
   unknown results, and a missing abstract.

The explicit Review → Maybe policy is represented by `non_primary=unknown` with
`review_maybe:` evidence. The pure decision function recognizes that evidence
even if all positive criteria happen to be hits.

## Incremental behavior

Every rule has its own version. A repeated run skips stored versions. Bumping
one rule version evaluates only that rule; the combination is then cheaply
upserted using the current configured versions.

Toggling a criterion writes `audit_log` and creates decision-recomputation
screen runs for previously screened query scopes. No rule is reevaluated and no
`machine_screening` row is added.

## Report

```bash
cd backend
python scripts/screening_report.py --query-version-id 1
```

The report prints hit/no-hit/unknown totals for every criterion and
Include/Exclude/Maybe totals for the current decision version.

## Known failure modes

- Records awaiting MeSH indexing often remain unknown unless clear fallback
  language is present.
- Keyword fallbacks can miss synonyms and can misread unusual context.
- `langdetect` is unreliable on short text; short records are kept unknown.
- Language metadata describes the indexed publication and may not describe all
  supplementary content.
- Age words can describe background populations rather than enrolled patients.
- Trial keywords can occur in protocols, secondary analyses, or discussion of
  another study.
- Review publication types are not uniformly assigned across providers.
- A topical mention does not prove that a GLP-1 drug is the tested intervention.
- Machine decisions are triage aids; human overrides are intentionally handled
  separately and take precedence when that workflow is added.
