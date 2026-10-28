# Mini-Rayyan Metrics Report (Multi-Topic Evaluation)

> **Generated:** 2026-10-28 11:06  
> **Topics Evaluated:**  
>   1. Demo: GLP-1 RCTs (Query version: 3)  
>   2. SGLT2 inhibitors in Heart Failure (Query version: 4)  
> **All figures are ESTIMATES.** Gold-set samples are small (≤5 abstracts for PICO, 
> dedup_gold.json is a TODO for reviewer). Do not treat these numbers as validated performance claims.

## Editable Assumptions (reviewer-time estimates)

| Assumption | Value |
|---|---|
| Seconds per screened record | 30 s |
| Seconds per duplicate pair reviewed | 20 s |
| Seconds per PICO-tagged abstract | 90 s |

Edit these constants at the top of `scripts/metrics.py` and re-run.

## Step 1 — Fetch

| Metric | Baseline (manual) | Topic 1: Demo: GLP-1 RCTs | Topic 2: SGLT2 inhibitors in Heart Failure | Notes |
|---|---|---|---|---|
| Total records found | manual export | 4 | 5 | ESTIMATE |
| PubMed records | — | 2 | 4 | |
| Europe PMC records | — | 2 | 1 | |
| New stored this run | — | 4 | 5 | |
| Cross-source overlap | unknown | 0 | 1 | ESTIMATE: shared PMIDs |
| API calls | manual | 4 | 4 | 2 per source |
| Wall time | manual hours | — | — | |
| Time per 100 records | — | — | — | ESTIMATE |

**Verdict:** ✅ Worth it — automated dual-source retrieval in seconds vs hours of manual database exports.

## Step 2 — Deduplication

| Metric | Exact-only baseline | Topic 1: Demo: GLP-1 RCTs | Topic 2: SGLT2 inhibitors in Heart Failure | Notes |
|---|---|---|---|---|
| Exact duplicate pairs | 0 | 0 | 1 | PMID / DOI match |
| Extra from fuzzy | — | 0 | 0 | Title/author distance |
| Uncertain pairs (reviewer needed) | — | 0 | 0 | Score between thresholds |
| Human overrides | — | 0 | 0 | |
| Precision on gold set | — | TODO (tests/fixtures/dedup_gold.json) | TODO (tests/fixtures/dedup_gold.json) | Hand-checked 50-pair sample |
| Wall time | — | 15.6s | 16.3s | |

**Verdict:** ✅ Deduplication successfully caught cross-database overlap (e.g. Topic 2 caught PubMed + Europe PMC duplicate).

## Step 3 — Screening

| Metric | No automation | Topic 1: Demo: GLP-1 RCTs | Topic 2: SGLT2 inhibitors in Heart Failure | Notes |
|---|---|---|---|---|
| Records screened | 4 / 5 | 4 | 5 | |
| Machine-excluded | 0 | 1 | 1 | Reviewer can skip |
| Machine-include | 0 | 1 | 0 | High-confidence matches |
| Machine-maybe (needs review) | 4 / 5 | 2 | 4 | Needs human review |
| Metadata exclusions | — | 2 | 1 | Pub type, non-human, etc. |
| Algorithm exclusions | — | 6 | 3 | Keyword/abstract rules |
| Human overrides | 0 | 0 | 0 | From audit_log |
| Wall time | — | 18.2s | 16.3s | |

**Criterion Breakdown (Hits):**

| Criterion Code | Kind | Source | Topic 1: Demo: GLP-1 RCTs Hits | Topic 2: SGLT2 inhibitors in Heart Failure Hits |
|---|---|---|---|---|
| case_report | exclude | metadata | 0 | 0 |
| human_adult | include | metadata | 1 | 0 |
| non_english | exclude | metadata | 0 | 0 |
| non_human | exclude | metadata | 0 | 1 |
| non_primary | exclude | metadata | 1 | 0 |
| pediatric_only | exclude | metadata | 0 | 0 |
| simulation | exclude | algorithm | 0 | 0 |
| topic_match | include | algorithm | 4 | 0 |
| trial_design | include | algorithm | 2 | 3 |

**Verdict:** ✅ Worth it — automated exclusion filters out non-human and non-primary studies before human review.

## Step 4 — PICO Tagging

| Metric | No automation | Topic 1: Demo: GLP-1 RCTs | Topic 2: SGLT2 inhibitors in Heart Failure | Notes |
|---|---|---|---|---|
| Abstracts tagged | 0 | 2 | 3 | |
| Total spans generated | 0 | 25 | 23 | |
| Population (P) spans | 0 | 4 | 6 | Patient/condition spans |
| Intervention (I) spans | 0 | 11 | 0 | Drug/treatment spans |
| Comparison (C) spans | 0 | 9 | 11 | Placebo/control spans |
| Outcome (O) spans | 0 | 1 | 6 | Endpoint spans |
| Wall time | — | 8.2s | 7.3s | |

> Gold set evaluation (on 5 abstracts via `scripts/pico_eval.py`):

| Label | Precision | Recall | TP | FP | FN |
|---|---|---|---|---|---|
| P | 1.0 | 0.6 | 3 | 0 | 2 |
| I | 1.0 | 1.0 | 5 | 0 | 0 |
| C | 1.0 | 1.0 | 5 | 0 | 0 |
| O | 1.0 | 1.0 | 6 | 0 | 0 |

**Verdict:** ✅ High precision rule-based tagging provides immediate jump-start for reviewers.

## Step 5 — Human Effort & Time Saved (ESTIMATES)

| Category | Topic 1: Demo: GLP-1 RCTs | Topic 2: SGLT2 inhibitors in Heart Failure | Combined |
|---|---|---|---|
| Screening saved | 0.5 min | 0.5 min | 1.0 min |
| Screening cost | 1.5 min | 2.0 min | 3.5 min |
| Dedup saved | 0.0 min | 0.3 min | 0.3 min |
| Dedup cost | 0.0 min | 0.0 min | 0.0 min |
| PICO saved | 3.0 min | 4.5 min | 7.5 min |
| PICO cost | 0.0 min | 0.0 min | 0.0 min |
| **Net Time Saved** | **2.0 min** | **3.3 min** | **5.3 min** |

| Audit Metric | Topic 1 | Topic 2 | Combined |
|---|---|---|---|
| Machine decisions | 4 | 5 | 9 |
| Human overrides | 0 | 0 | 0 |
| Override rate | 0.0 | 0.0 | — |
| PICO edits | 0 | 0 | 0 |

## Runtime per step

| Step | Topic 1: Demo: GLP-1 RCTs | Topic 2: SGLT2 inhibitors in Heart Failure |
|---|---|---|
| Fetch | — | — |
| Dedup | 15.6s | 16.3s |
| Screen | 18.2s | 16.3s |
| PICO | 8.2s | 7.3s |

## Known Failure Cases

1. **Fetch**: Single-batch requests; >1000 records need cursor pagination (not implemented).
2. **Fetch**: Europe PMC 503 occurred 2026-10-27; retry logic (`http_client.py`) now handles it.
3. **Dedup**: Fuzzy blocking by year+first_author misses cross-database duplicates with different years or missing author data.
4. **Dedup**: `dedup_gold.json` has a 50-pair template with TODO flag awaiting human labelling.
5. **Screening**: Abstract-based rules fire 'unknown' when abstract is missing; records default to 'maybe'.
6. **Screening**: No sentence-level disambiguation; multi-claim abstracts can produce false positives.
7. **PICO**: Exact-offset gold evaluation is strict; rule spans often match semantically but not at the exact character boundary.
8. **PICO**: Gold set has only 5 abstracts — P/R estimates have high variance.
9. **Level 2 loop**: Activation uses run_log export step; filtering must check `params.action = 'activate'`.
10. **General**: All timing figures from runs on small corpora; not reproducible hardware benchmarks.

---
*Report generated by `scripts/metrics.py`. Re-run after any pipeline change.*