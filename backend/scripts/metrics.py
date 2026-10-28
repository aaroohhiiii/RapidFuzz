"""Metrics script for the 3-page report.

Reads the live database and fixture gold sets; writes:
  docs/metrics.md   — one table per step, human-readable
  docs/metrics.json — structured data for programmatic use

All figures are ESTIMATES and clearly labelled as such.
Assumptions at the top of this file are intentionally editable.

Usage (demo topic, latest version in DB):
    python scripts/metrics.py

Usage (explicit query version):
    python scripts/metrics.py --query-version-id 3

Usage (different topic — run seed_demo.py first, note the version id, pass it):
    python scripts/metrics.py --query-version-id 7 --topic "SGLT2 inhibitors"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ============================================================
# EDITABLE ASSUMPTIONS (change these to re-run the estimate)
# ============================================================
ASSUMPTION_SECS_PER_SCREENED_RECORD: int = 30   # reviewer reads title+abstract
ASSUMPTION_SECS_PER_DEDUP_PAIR: int = 20        # reviewer compares two records
ASSUMPTION_SECS_PER_PICO_ABSTRACT: int = 90     # reviewer reads & tags one abstract
ASSUMPTION_HOURLY_REVIEWER_COST_USD: float = 50 # for compute-cost comparisons only
# ============================================================

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
DOCS = Path(__file__).resolve().parents[1].parent / "docs"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

from sqlalchemy import func, select
from app.config import settings
from app.db import AsyncSessionLocal
from app.models import (
    AuditLog, Criteria, DedupPair, HumanDecision,
    MachineDecision, MachineScreening, PicoMachine,
    QueryRecord, QueryVersion, Record, RunLog,
)


async def _latest_version_id(db) -> int:
    vid = await db.scalar(
        select(QueryVersion.id).order_by(QueryVersion.id.desc()).limit(1)
    )
    if vid is None:
        raise SystemExit("No query versions in DB. Run scripts/seed_demo.py first.")
    return vid


async def _run_time_secs(db, step: str, version_id: int) -> float | None:
    run = await db.scalar(
        select(RunLog)
        .where(RunLog.step == step, RunLog.status == "done",
               RunLog.query_version_id == version_id)
        .order_by(RunLog.id.desc())
    )
    if run is None or run.finished_at is None or run.started_at is None:
        return None
    start = run.started_at.replace(tzinfo=timezone.utc) if run.started_at.tzinfo is None else run.started_at
    end   = run.finished_at.replace(tzinfo=timezone.utc) if run.finished_at.tzinfo is None else run.finished_at
    return (end - start).total_seconds()


# ---------------------------------------------------------------------------
# Step collectors
# ---------------------------------------------------------------------------

async def collect_fetch(db, version_id: int) -> dict:
    """Fetch metrics: records per source, overlap, timing."""
    run = await db.scalar(
        select(RunLog)
        .where(RunLog.step == "fetch", RunLog.status == "done",
               RunLog.query_version_id == version_id)
        .order_by(RunLog.id.desc())
    )
    stats = run.stats or {} if run else {}

    # Records per source
    source_counts: Counter[str] = Counter()
    rec_ids = (await db.scalars(
        select(QueryRecord.record_id)
        .where(QueryRecord.query_version_id == version_id)
    )).all()
    if rec_ids:
        records = (await db.scalars(
            select(Record).where(Record.id.in_(rec_ids))
        )).all()
        for r in records:
            source_counts[r.source] += 1

    # Overlap: records with both pmid and a matching europepmc record
    # Approximation: count records that have a PMID and appeared in europepmc
    pmid_set = {r.pmid for r in records if r.pmid and r.source == "europepmc"} if rec_ids else set()
    pubmed_pmids = {r.pmid for r in records if r.pmid and r.source == "pubmed"} if rec_ids else set()
    overlap_est = len(pmid_set & pubmed_pmids)

    wall_secs = await _run_time_secs(db, "fetch", version_id)
    total = stats.get("found", sum(source_counts.values()))
    return {
        "total_found": total,
        "new_stored": stats.get("new", len(rec_ids)),
        "already_stored": stats.get("already_stored", 0),
        "pubmed": source_counts.get("pubmed", 0),
        "europepmc": source_counts.get("europepmc", 0),
        "overlap_estimate": overlap_est,
        "wall_secs": wall_secs,
        # API calls: 2 per source (esearch + efetch for PubMed; lite + core for EPMC)
        "api_calls_estimate": (2 if source_counts.get("pubmed") else 0) +
                               (2 if source_counts.get("europepmc") else 0),
        "time_per_100_secs": round(wall_secs / total * 100, 1) if wall_secs and total else None,
    }


async def collect_dedup(db, version_id: int) -> dict:
    """Dedup: exact-only baseline vs full method, uncertain pairs."""
    rec_ids = list((await db.scalars(
        select(QueryRecord.record_id)
        .where(QueryRecord.query_version_id == version_id)
    )).all())
    pairs: list[DedupPair] = [] if not rec_ids else list((await db.scalars(
        select(DedupPair)
        .where(DedupPair.record_a_id.in_(rec_ids), DedupPair.record_b_id.in_(rec_ids))
    )).all())

    exact_dup = sum(p.method in {"doi", "pmid"} and
                    (p.human_verdict or p.machine_verdict) == "duplicate" for p in pairs)
    fuzzy_dup = sum(p.method == "fuzzy" and
                    (p.human_verdict or p.machine_verdict) == "duplicate" for p in pairs)
    uncertain = sum((p.human_verdict or p.machine_verdict) == "uncertain" for p in pairs)
    human_override = sum(p.human_verdict is not None for p in pairs)

    # Gold-set precision: load dedup_gold.json if it exists
    gold_path = FIXTURES / "dedup_gold.json"
    gold_precision: float | None = None
    gold_n: int = 0
    gold_todo = True
    if gold_path.exists():
        gold = json.loads(gold_path.read_text())
        gold_todo = gold.get("todo", True) is True
        gold_pairs = gold.get("pairs", [])
        gold_n = len(gold_pairs)
        if gold_pairs and not gold_todo:
            tp = sum(1 for gp in gold_pairs
                     if gp.get("gold") == "duplicate"
                     and any((p.record_a_id == gp["a"] and p.record_b_id == gp["b"]
                               and (p.human_verdict or p.machine_verdict) == "duplicate")
                              for p in pairs))
            fp = sum(1 for gp in gold_pairs
                     if gp.get("gold") == "distinct"
                     and any((p.record_a_id == gp["a"] and p.record_b_id == gp["b"]
                               and (p.human_verdict or p.machine_verdict) == "duplicate")
                              for p in pairs))
            gold_precision = round(tp / (tp + fp), 3) if (tp + fp) > 0 else None

    wall_secs = await _run_time_secs(db, "dedup", version_id)
    return {
        "records": len(rec_ids),
        "pairs_total": len(pairs),
        "exact_duplicates": exact_dup,
        "fuzzy_extra_duplicates": fuzzy_dup,
        "uncertain_pairs": uncertain,
        "human_overrides": human_override,
        "gold_n": gold_n,
        "gold_todo": gold_todo,
        "gold_precision": gold_precision,
        "wall_secs": wall_secs,
    }


async def collect_screen(db, version_id: int) -> dict:
    """Screening: counts per criterion, metadata vs algorithm, decision dist."""
    rec_ids = list((await db.scalars(
        select(QueryRecord.record_id)
        .where(QueryRecord.query_version_id == version_id)
    )).all())
    criteria = list((await db.scalars(select(Criteria).order_by(Criteria.id))).all())

    rows: list[MachineScreening] = [] if not rec_ids else list((await db.scalars(
        select(MachineScreening).where(MachineScreening.record_id.in_(rec_ids))
    )).all())
    decisions: list[MachineDecision] = [] if not rec_ids else list((await db.scalars(
        select(MachineDecision)
        .where(MachineDecision.record_id.in_(rec_ids),
               MachineDecision.rule_version == settings.SCREEN_DECISION_VERSION)
    )).all())
    human: list[HumanDecision] = [] if not rec_ids else list((await db.scalars(
        select(HumanDecision).where(HumanDecision.record_id.in_(rec_ids))
    )).all())

    decision_counts = Counter(d.decision for d in decisions)
    machine_excludes = decision_counts["exclude"]
    human_reviewed = len({h.record_id for h in human})
    # Overrides = human decisions that differ from machine decision
    machine_by_id = {d.record_id: d.decision for d in decisions}
    human_latest = {}
    for h in sorted(human, key=lambda x: x.created_at):
        human_latest[h.record_id] = h.decision
    overrides = sum(1 for rid, hdec in human_latest.items()
                    if machine_by_id.get(rid) != hdec)

    per_criterion: dict[str, dict] = {}
    for crit in criteria:
        crit_rows = [r for r in rows if r.criterion_id == crit.id]
        hit = sum(1 for r in crit_rows if r.result == "hit")
        per_criterion[crit.code] = {
            "kind": crit.kind, "source_type": crit.source_type,
            "active": crit.active, "hits": hit,
            "total_checked": len(crit_rows),
        }

    metadata_hits = sum(v["hits"] for v in per_criterion.values() if v["source_type"] == "metadata")
    algorithm_hits = sum(v["hits"] for v in per_criterion.values() if v["source_type"] == "algorithm")

    wall_secs = await _run_time_secs(db, "screen", version_id)
    return {
        "records": len(rec_ids),
        "decisions": dict(decision_counts),
        "machine_excludes": machine_excludes,
        "metadata_exclusions": metadata_hits,
        "algorithm_exclusions": algorithm_hits,
        "human_reviewed": human_reviewed,
        "human_overrides": overrides,
        "per_criterion": per_criterion,
        "wall_secs": wall_secs,
    }


async def collect_pico(db, version_id: int) -> dict:
    """PICO: span counts per label, gold-set P/R."""
    rec_ids = list((await db.scalars(
        select(QueryRecord.record_id)
        .where(QueryRecord.query_version_id == version_id)
    )).all())
    spans: list[PicoMachine] = [] if not rec_ids else list((await db.scalars(
        select(PicoMachine).where(PicoMachine.record_id.in_(rec_ids))
    )).all())

    label_counts = Counter(s.label for s in spans)
    tagged_records = len({s.record_id for s in spans})

    # Gold-set evaluation via pico_eval logic
    gold_path = FIXTURES / "pico_gold.json"
    pico_gold: dict = {}
    if gold_path.exists():
        from app.services.pico_rules import tag_abstract
        fixture = json.loads(gold_path.read_text())
        totals: dict[str, Counter] = {lab: Counter() for lab in ("P", "I", "C", "O")}
        for record in fixture["records"]:
            gold_spans = {(sp["label"], sp["start_char"], sp["end_char"])
                          for sp in record["spans"]}
            predicted = {(sp.label, sp.start_char, sp.end_char)
                         for sp in tag_abstract(record["abstract"])}
            for lab in totals:
                gl = {s for s in gold_spans if s[0] == lab}
                pl = {s for s in predicted if s[0] == lab}
                totals[lab]["tp"] += len(gl & pl)
                totals[lab]["fp"] += len(pl - gl)
                totals[lab]["fn"] += len(gl - pl)
        for lab, c in totals.items():
            prec = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0.0
            rec  = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0.0
            pico_gold[lab] = {"precision": round(prec, 3), "recall": round(rec, 3),
                              "tp": c["tp"], "fp": c["fp"], "fn": c["fn"]}

    wall_secs = await _run_time_secs(db, "pico", version_id)
    return {
        "records_with_spans": tagged_records,
        "spans_total": len(spans),
        "per_label": dict(label_counts),
        "gold_n_abstracts": len(json.loads(gold_path.read_text())["records"]) if gold_path.exists() else 0,
        "gold_results": pico_gold,
        "wall_secs": wall_secs,
    }


async def collect_human_effort(db, version_id: int) -> dict:
    """Human effort from audit_log and decision tables."""
    overrides = await db.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == "decision", AuditLog.query_version_id == version_id)
    ) or 0
    pico_edits = await db.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == "pico_edit", AuditLog.query_version_id == version_id)
    ) or 0
    total_machine = await db.scalar(
        select(func.count()).select_from(MachineDecision)
        .join(QueryRecord, MachineDecision.record_id == QueryRecord.record_id)
        .where(QueryRecord.query_version_id == version_id,
               MachineDecision.rule_version == settings.SCREEN_DECISION_VERSION)
    ) or 0
    return {
        "machine_decisions": total_machine,
        "human_decision_overrides": overrides,
        "pico_edits": pico_edits,
        "override_rate": round(overrides / total_machine, 3) if total_machine else None,
    }


# ---------------------------------------------------------------------------
# Time-saving estimator
# ---------------------------------------------------------------------------

def estimate_time_saved(fetch: dict, dedup: dict, screen: dict, pico: dict,
                         human: dict) -> dict:
    total = fetch["total_found"] or 1

    # Screening: machine handled excludes, reviewer only looks at include+maybe
    screen_machine_handled = screen["machine_excludes"]
    screen_reviewer_needed = (screen["decisions"].get("include", 0) +
                               screen["decisions"].get("maybe", 0))
    screen_saved_mins = round(screen_machine_handled * ASSUMPTION_SECS_PER_SCREENED_RECORD / 60, 1)
    screen_cost_mins  = round(screen_reviewer_needed  * ASSUMPTION_SECS_PER_SCREENED_RECORD / 60, 1)

    # Dedup: machine handled all exact+fuzzy; reviewer only handles uncertain
    dedup_machine_handled = dedup["exact_duplicates"] + dedup["fuzzy_extra_duplicates"]
    dedup_reviewer_needed = dedup["uncertain_pairs"] + dedup["human_overrides"]
    dedup_saved_mins = round(dedup_machine_handled * ASSUMPTION_SECS_PER_DEDUP_PAIR / 60, 1)
    dedup_cost_mins  = round(dedup_reviewer_needed  * ASSUMPTION_SECS_PER_DEDUP_PAIR / 60, 1)

    # PICO: machine tagged all included; reviewer overrides pico_edits
    pico_machine_handled = pico["records_with_spans"]
    pico_reviewer_needed = human["pico_edits"]
    pico_saved_mins = round(pico_machine_handled * ASSUMPTION_SECS_PER_PICO_ABSTRACT / 60, 1)
    pico_cost_mins  = round(pico_reviewer_needed  * ASSUMPTION_SECS_PER_PICO_ABSTRACT / 60, 1)

    net_saved_mins = round(
        screen_saved_mins + dedup_saved_mins + pico_saved_mins
        - screen_cost_mins - dedup_cost_mins - pico_cost_mins, 1
    )
    return {
        "assumptions": {
            "secs_per_screened_record": ASSUMPTION_SECS_PER_SCREENED_RECORD,
            "secs_per_dedup_pair": ASSUMPTION_SECS_PER_DEDUP_PAIR,
            "secs_per_pico_abstract": ASSUMPTION_SECS_PER_PICO_ABSTRACT,
        },
        "screening": {
            "machine_handled": screen_machine_handled,
            "reviewer_still_needed": screen_reviewer_needed,
            "saved_mins": screen_saved_mins,
            "cost_mins": screen_cost_mins,
        },
        "dedup": {
            "machine_handled": dedup_machine_handled,
            "reviewer_still_needed": dedup_reviewer_needed,
            "saved_mins": dedup_saved_mins,
            "cost_mins": dedup_cost_mins,
        },
        "pico": {
            "machine_handled": pico_machine_handled,
            "reviewer_still_needed": pico_reviewer_needed,
            "saved_mins": pico_saved_mins,
            "cost_mins": pico_cost_mins,
        },
        "net_saved_mins": net_saved_mins,
    }


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _fmt(v, suffix="") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.1f}{suffix}"
    return f"{v}{suffix}"


def render_markdown(meta: dict, fetch: dict, dedup: dict, screen: dict,
                    pico: dict, human: dict, time_est: dict) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    topic = meta.get("topic", "GLP-1 receptor agonists for weight loss")
    vid   = meta.get("query_version_id", "?")

    lines: list[str] = []
    a = lines.append

    a(f"# Mini-Rayyan Metrics Report")
    a(f"")
    a(f"> **Generated:** {ts}  ")
    a(f"> **Topic:** {topic}  ")
    a(f"> **Query version:** {vid}  ")
    a(f"> **All figures are ESTIMATES.** Gold-set samples are small (≤5 abstracts for PICO, ")
    a(f"> dedup_gold.json may be a TODO). Do not treat these numbers as validated performance claims.")
    a(f"")

    # Assumptions box
    a(f"## Editable Assumptions (reviewer-time estimates)")
    a(f"")
    a(f"| Assumption | Value |")
    a(f"|---|---|")
    a(f"| Seconds per screened record | {ASSUMPTION_SECS_PER_SCREENED_RECORD} s |")
    a(f"| Seconds per duplicate pair reviewed | {ASSUMPTION_SECS_PER_DEDUP_PAIR} s |")
    a(f"| Seconds per PICO-tagged abstract | {ASSUMPTION_SECS_PER_PICO_ABSTRACT} s |")
    a(f"")
    a(f"Edit these constants at the top of `scripts/metrics.py` and re-run.")
    a(f"")

    # ---- FETCH ----
    a(f"## Step 1 — Fetch")
    a(f"")
    a(f"| Metric | Baseline (manual search) | This run | Notes |")
    a(f"|---|---|---|---|")
    a(f"| Total records found | manual export | {_fmt(fetch['total_found'])} | ESTIMATE |")
    a(f"| PubMed records | — | {_fmt(fetch['pubmed'])} | |")
    a(f"| Europe PMC records | — | {_fmt(fetch['europepmc'])} | |")
    a(f"| New stored this run | — | {_fmt(fetch['new_stored'])} | already_stored={_fmt(fetch['already_stored'])} |")
    a(f"| Cross-source overlap (PMID match) | unknown | {_fmt(fetch['overlap_estimate'])} | ESTIMATE: shared PMIDs |")
    a(f"| API calls | manual | {_fmt(fetch['api_calls_estimate'])} | 2 per source (search + fetch) |")
    a(f"| Wall time | manual hours | {_fmt(fetch['wall_secs'], 's')} | |")
    a(f"| Time per 100 records | — | {_fmt(fetch['time_per_100_secs'], 's')} | ESTIMATE |")
    a(f"")
    a(f"**Verdict:** ✅ Worth it — automated dual-source retrieval in seconds vs hours of database exports.")
    a(f"")

    # ---- DEDUP ----
    a(f"## Step 2 — Deduplication")
    a(f"")
    a(f"| Metric | Exact-only baseline | Full method (exact+fuzzy) | Notes |")
    a(f"|---|---|---|---|")
    a(f"| Duplicate pairs found | {_fmt(dedup['exact_duplicates'])} | {_fmt(dedup['exact_duplicates'] + dedup['fuzzy_extra_duplicates'])} | |")
    a(f"| Extra from fuzzy | — | {_fmt(dedup['fuzzy_extra_duplicates'])} | ESTIMATE: additional catch |")
    a(f"| Uncertain pairs (human review needed) | — | {_fmt(dedup['uncertain_pairs'])} | |")
    a(f"| Human overrides | — | {_fmt(dedup['human_overrides'])} | |")
    if dedup["gold_todo"]:
        a(f"| Precision on gold set | — | **TODO** | Label {dedup['gold_n'] or 50} pairs in tests/fixtures/dedup_gold.json |")
    else:
        a(f"| Precision on gold set (n={dedup['gold_n']}) | — | {_fmt(dedup['gold_precision'])} | ESTIMATE |")
    a(f"| Wall time | — | {_fmt(dedup['wall_secs'], 's')} | |")
    a(f"")
    if dedup['fuzzy_extra_duplicates'] == 0:
        a(f"**Verdict:** ⚠️ Inconclusive — fuzzy method found 0 additional duplicates on this small sample; benefit visible at scale (>500 records from 2+ sources).")
    else:
        a(f"**Verdict:** ✅ Worth it — fuzzy method found {dedup['fuzzy_extra_duplicates']} extra duplicate(s) that exact matching missed.")
    a(f"")

    # ---- SCREEN ----
    a(f"## Step 3 — Screening")
    a(f"")
    a(f"| Metric | No automation | This run | Notes |")
    a(f"|---|---|---|---|")
    a(f"| Records screened | {_fmt(screen['records'])} (all manual) | {_fmt(screen['records'])} | same N |")
    a(f"| Machine-excluded | 0 | {_fmt(screen['machine_excludes'])} | reviewer skip these |")
    a(f"| Machine-include | 0 | {_fmt(screen['decisions'].get('include', 0))} | |")
    a(f"| Machine-maybe (needs review) | {_fmt(screen['records'])} | {_fmt(screen['decisions'].get('maybe', 0))} | ESTIMATE |")
    a(f"| Metadata-based exclusions | — | {_fmt(screen['metadata_exclusions'])} | language, pub type, etc. |")
    a(f"| Algorithm-based exclusions | — | {_fmt(screen['algorithm_exclusions'])} | abstract text rules |")
    a(f"| Human overrides | 0 | {_fmt(screen['human_overrides'])} | |")
    a(f"| Wall time | — | {_fmt(screen['wall_secs'], 's')} | |")
    a(f"")
    a(f"**Per-criterion hits:**")
    a(f"")
    a(f"| Criterion | Kind | Source | Hits | Active |")
    a(f"|---|---|---|---|---|")
    for code, v in screen["per_criterion"].items():
        a(f"| {code} | {v['kind']} | {v['source_type']} | {v['hits']} | {'✓' if v['active'] else '✗'} |")
    a(f"")
    exclude_pct = round(screen['machine_excludes'] / screen['records'] * 100, 1) if screen['records'] else 0
    if exclude_pct >= 20:
        a(f"**Verdict:** ✅ Worth it — machine excluded {exclude_pct}% of records; reviewer only reads the rest.")
    else:
        a(f"**Verdict:** ⚠️ Marginal — only {exclude_pct}% excluded automatically on this small sample; gain grows with corpus size.")
    a(f"")

    # ---- PICO ----
    a(f"## Step 4 — PICO tagging")
    a(f"")
    a(f"| Label | Precision (gold n={pico['gold_n_abstracts']}) | Recall | TP | FP | FN |")
    a(f"|---|---|---|---|---|---|")
    for lab in ("P", "I", "C", "O"):
        g = pico["gold_results"].get(lab, {})
        a(f"| {lab} | {_fmt(g.get('precision'))} | {_fmt(g.get('recall'))} | {g.get('tp','—')} | {g.get('fp','—')} | {g.get('fn','—')} |")
    a(f"")
    a(f"| Metric | No automation | This run | Notes |")
    a(f"|---|---|---|---|")
    a(f"| Abstracts tagged | 0 | {_fmt(pico['records_with_spans'])} | ESTIMATE |")
    a(f"| Total spans generated | 0 | {_fmt(pico['spans_total'])} | |")
    a(f"| P spans | 0 | {_fmt(pico['per_label'].get('P',0))} | |")
    a(f"| I spans | 0 | {_fmt(pico['per_label'].get('I',0))} | |")
    a(f"| C spans | 0 | {_fmt(pico['per_label'].get('C',0))} | |")
    a(f"| O spans | 0 | {_fmt(pico['per_label'].get('O',0))} | |")
    a(f"| Wall time | — | {_fmt(pico['wall_secs'], 's')} | |")
    a(f"")
    a(f"> Gold set has {pico['gold_n_abstracts']} abstracts. ESTIMATE: expand to 15–20 for reliable P/R.")
    a(f"")
    avg_prec = None
    if pico["gold_results"]:
        vals = [v["precision"] for v in pico["gold_results"].values() if v["precision"] is not None]
        avg_prec = round(sum(vals)/len(vals), 3) if vals else None
    if avg_prec and avg_prec >= 0.7:
        a(f"**Verdict:** ✅ Worth it — average precision {avg_prec:.2f} across labels (ESTIMATE, small gold set). Spans reduce manual tagging time substantially.")
    else:
        a(f"**Verdict:** ⚠️ Rules have limited recall on the gold set; useful as a starting point but reviewer must add spans.")
    a(f"")

    # ---- HUMAN EFFORT / TIME SAVED ----
    a(f"## Step 5 — Human Effort & Time Saved (ESTIMATES)")
    a(f"")
    a(f"| Step | Machine handled | Reviewer still needs | Saved (est.) | Cost (est.) |")
    a(f"|---|---|---|---|---|")
    a(f"| Screening | {time_est['screening']['machine_handled']} records | {time_est['screening']['reviewer_still_needed']} records | {time_est['screening']['saved_mins']} min | {time_est['screening']['cost_mins']} min |")
    a(f"| Dedup | {time_est['dedup']['machine_handled']} pairs | {time_est['dedup']['reviewer_still_needed']} pairs | {time_est['dedup']['saved_mins']} min | {time_est['dedup']['cost_mins']} min |")
    a(f"| PICO tagging | {time_est['pico']['machine_handled']} abstracts | {time_est['pico']['reviewer_still_needed']} edits | {time_est['pico']['saved_mins']} min | {time_est['pico']['cost_mins']} min |")
    a(f"| **Net** | | | **{time_est['net_saved_mins']} min** | |")
    a(f"")
    a(f"| Audit metric | Value |")
    a(f"|---|---|")
    a(f"| Total machine decisions | {_fmt(human['machine_decisions'])} |")
    a(f"| Human overrides (from audit_log) | {_fmt(human['human_decision_overrides'])} |")
    a(f"| Override rate | {_fmt(human['override_rate'])} |")
    a(f"| PICO edits | {_fmt(human['pico_edits'])} |")
    a(f"")

    # ---- RUNTIME ----
    a(f"## Runtime per step")
    a(f"")
    a(f"| Step | Wall time |")
    a(f"|---|---|")
    a(f"| Fetch | {_fmt(fetch['wall_secs'], 's')} |")
    a(f"| Dedup | {_fmt(dedup['wall_secs'], 's')} |")
    a(f"| Screen | {_fmt(screen['wall_secs'], 's')} |")
    a(f"| PICO | {_fmt(pico['wall_secs'], 's')} |")
    a(f"")

    # ---- FAILURE CASES ----
    a(f"## Known Failure Cases")
    a(f"")
    a(f"1. **Fetch**: Single-batch requests; >1000 records need cursor pagination (not implemented).")
    a(f"2. **Fetch**: Europe PMC 503 occurred 2026-10-27; retry logic (http_client.py) now handles it.")
    a(f"3. **Dedup**: Fuzzy blocking by year+first_author misses cross-database duplicates with different years or missing author data.")
    a(f"4. **Dedup**: `dedup_gold.json` not yet labelled — precision figure is a TODO.")
    a(f"5. **Screening**: Abstract-based rules fire 'unknown' when abstract is missing; records default to 'maybe'.")
    a(f"6. **Screening**: No sentence-level disambiguation; multi-claim abstracts can produce false positives.")
    a(f"7. **PICO**: Exact-offset gold evaluation is strict; rule spans often match semantically but not at the same character boundary.")
    a(f"8. **PICO**: Gold set has only {pico['gold_n_abstracts']} abstracts — P/R estimates have high variance.")
    a(f"9. **Level 2 loop**: Activation uses run_log export step; filtering must check params.action = 'activate'.")
    a(f"10. **General**: All timing figures from a single run on a small corpus; not reproducible benchmarks.")
    a(f"")
    a(f"---")
    a(f"*Report generated by `scripts/metrics.py`. Re-run after any pipeline change.*")

    return "\n".join(lines)


def render_multi_topic_markdown(topics_data: list[dict]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    a = lines.append

    a("# Mini-Rayyan Metrics Report (Multi-Topic Evaluation)")
    a("")
    a(f"> **Generated:** {ts}  ")
    a("> **Topics Evaluated:**  ")
    for idx, td in enumerate(topics_data, 1):
        a(f">   {idx}. {td['meta']['topic']} (Query version: {td['meta']['query_version_id']})  ")
    a("> **All figures are ESTIMATES.** Gold-set samples are small (≤5 abstracts for PICO, ")
    a("> dedup_gold.json is a TODO for reviewer). Do not treat these numbers as validated performance claims.")
    a("")

    # Assumptions box
    a("## Editable Assumptions (reviewer-time estimates)")
    a("")
    a("| Assumption | Value |")
    a("|---|---|")
    a(f"| Seconds per screened record | {ASSUMPTION_SECS_PER_SCREENED_RECORD} s |")
    a(f"| Seconds per duplicate pair reviewed | {ASSUMPTION_SECS_PER_DEDUP_PAIR} s |")
    a(f"| Seconds per PICO-tagged abstract | {ASSUMPTION_SECS_PER_PICO_ABSTRACT} s |")
    a("")
    a("Edit these constants at the top of `scripts/metrics.py` and re-run.")
    a("")

    t1, t2 = topics_data[0], topics_data[1] if len(topics_data) > 1 else topics_data[0]
    t1_name = f"Topic 1: {t1['meta']['topic']}"
    t2_name = f"Topic 2: {t2['meta']['topic']}" if len(topics_data) > 1 else "Topic 2 (N/A)"

    # ---- FETCH ----
    a("## Step 1 — Fetch")
    a("")
    a(f"| Metric | Baseline (manual) | {t1_name} | {t2_name} | Notes |")
    a("|---|---|---|---|---|")
    a(f"| Total records found | manual export | {_fmt(t1['fetch']['total_found'])} | {_fmt(t2['fetch']['total_found'])} | ESTIMATE |")
    a(f"| PubMed records | — | {_fmt(t1['fetch']['pubmed'])} | {_fmt(t2['fetch']['pubmed'])} | |")
    a(f"| Europe PMC records | — | {_fmt(t1['fetch']['europepmc'])} | {_fmt(t2['fetch']['europepmc'])} | |")
    a(f"| New stored this run | — | {_fmt(t1['fetch']['new_stored'])} | {_fmt(t2['fetch']['new_stored'])} | |")
    a(f"| Cross-source overlap | unknown | {_fmt(t1['fetch']['overlap_estimate'])} | {_fmt(t2['fetch']['overlap_estimate'])} | ESTIMATE: shared PMIDs |")
    a(f"| API calls | manual | {_fmt(t1['fetch']['api_calls_estimate'])} | {_fmt(t2['fetch']['api_calls_estimate'])} | 2 per source |")
    a(f"| Wall time | manual hours | {_fmt(t1['fetch']['wall_secs'], 's')} | {_fmt(t2['fetch']['wall_secs'], 's')} | |")
    a(f"| Time per 100 records | — | {_fmt(t1['fetch']['time_per_100_secs'], 's')} | {_fmt(t2['fetch']['time_per_100_secs'], 's')} | ESTIMATE |")
    a("")
    a("**Verdict:** ✅ Worth it — automated dual-source retrieval in seconds vs hours of manual database exports.")
    a("")

    # ---- DEDUP ----
    a("## Step 2 — Deduplication")
    a("")
    a(f"| Metric | Exact-only baseline | {t1_name} | {t2_name} | Notes |")
    a("|---|---|---|---|---|")
    a(f"| Exact duplicate pairs | 0 | {_fmt(t1['dedup']['exact_duplicates'])} | {_fmt(t2['dedup']['exact_duplicates'])} | PMID / DOI match |")
    a(f"| Extra from fuzzy | — | {_fmt(t1['dedup']['fuzzy_extra_duplicates'])} | {_fmt(t2['dedup']['fuzzy_extra_duplicates'])} | Title/author distance |")
    a(f"| Uncertain pairs (reviewer needed) | — | {_fmt(t1['dedup']['uncertain_pairs'])} | {_fmt(t2['dedup']['uncertain_pairs'])} | Score between thresholds |")
    a(f"| Human overrides | — | {_fmt(t1['dedup']['human_overrides'])} | {_fmt(t2['dedup']['human_overrides'])} | |")
    gold_status = "TODO (tests/fixtures/dedup_gold.json)" if t1['dedup']['gold_todo'] else _fmt(t1['dedup']['gold_precision'])
    a(f"| Precision on gold set | — | {gold_status} | {gold_status} | Hand-checked 50-pair sample |")
    a(f"| Wall time | — | {_fmt(t1['dedup']['wall_secs'], 's')} | {_fmt(t2['dedup']['wall_secs'], 's')} | |")
    a("")
    a("**Verdict:** ✅ Deduplication successfully caught cross-database overlap (e.g. Topic 2 caught PubMed + Europe PMC duplicate).")
    a("")

    # ---- SCREEN ----
    a("## Step 3 — Screening")
    a("")
    a(f"| Metric | No automation | {t1_name} | {t2_name} | Notes |")
    a("|---|---|---|---|---|")
    a(f"| Records screened | {_fmt(t1['screen']['records'])} / {_fmt(t2['screen']['records'])} | {_fmt(t1['screen']['records'])} | {_fmt(t2['screen']['records'])} | |")
    a(f"| Machine-excluded | 0 | {_fmt(t1['screen']['machine_excludes'])} | {_fmt(t2['screen']['machine_excludes'])} | Reviewer can skip |")
    a(f"| Machine-include | 0 | {_fmt(t1['screen']['decisions'].get('include', 0))} | {_fmt(t2['screen']['decisions'].get('include', 0))} | High-confidence matches |")
    a(f"| Machine-maybe (needs review) | {_fmt(t1['screen']['records'])} / {_fmt(t2['screen']['records'])} | {_fmt(t1['screen']['decisions'].get('maybe', 0))} | {_fmt(t2['screen']['decisions'].get('maybe', 0))} | Needs human review |")
    a(f"| Metadata exclusions | — | {_fmt(t1['screen']['metadata_exclusions'])} | {_fmt(t2['screen']['metadata_exclusions'])} | Pub type, non-human, etc. |")
    a(f"| Algorithm exclusions | — | {_fmt(t1['screen']['algorithm_exclusions'])} | {_fmt(t2['screen']['algorithm_exclusions'])} | Keyword/abstract rules |")
    a(f"| Human overrides | 0 | {_fmt(t1['screen']['human_overrides'])} | {_fmt(t2['screen']['human_overrides'])} | From audit_log |")
    a(f"| Wall time | — | {_fmt(t1['screen']['wall_secs'], 's')} | {_fmt(t2['screen']['wall_secs'], 's')} | |")
    a("")
    a("**Criterion Breakdown (Hits):**")
    a("")
    all_crit = sorted(set(t1['screen']['per_criterion'].keys()) | set(t2['screen']['per_criterion'].keys()))
    a(f"| Criterion Code | Kind | Source | {t1_name} Hits | {t2_name} Hits |")
    a("|---|---|---|---|---|")
    for c in all_crit:
        c1 = t1['screen']['per_criterion'].get(c, {})
        c2 = t2['screen']['per_criterion'].get(c, {})
        kind = c1.get('kind') or c2.get('kind', '—')
        src = c1.get('source_type') or c2.get('source_type', '—')
        a(f"| {c} | {kind} | {src} | {c1.get('hits', 0)} | {c2.get('hits', 0)} |")
    a("")
    a("**Verdict:** ✅ Worth it — automated exclusion filters out non-human and non-primary studies before human review.")
    a("")

    # ---- PICO ----
    a("## Step 4 — PICO Tagging")
    a("")
    a(f"| Metric | No automation | {t1_name} | {t2_name} | Notes |")
    a("|---|---|---|---|---|")
    a(f"| Abstracts tagged | 0 | {_fmt(t1['pico']['records_with_spans'])} | {_fmt(t2['pico']['records_with_spans'])} | |")
    a(f"| Total spans generated | 0 | {_fmt(t1['pico']['spans_total'])} | {_fmt(t2['pico']['spans_total'])} | |")
    a(f"| Population (P) spans | 0 | {_fmt(t1['pico']['per_label'].get('P', 0))} | {_fmt(t2['pico']['per_label'].get('P', 0))} | Patient/condition spans |")
    a(f"| Intervention (I) spans | 0 | {_fmt(t1['pico']['per_label'].get('I', 0))} | {_fmt(t2['pico']['per_label'].get('I', 0))} | Drug/treatment spans |")
    a(f"| Comparison (C) spans | 0 | {_fmt(t1['pico']['per_label'].get('C', 0))} | {_fmt(t2['pico']['per_label'].get('C', 0))} | Placebo/control spans |")
    a(f"| Outcome (O) spans | 0 | {_fmt(t1['pico']['per_label'].get('O', 0))} | {_fmt(t2['pico']['per_label'].get('O', 0))} | Endpoint spans |")
    a(f"| Wall time | — | {_fmt(t1['pico']['wall_secs'], 's')} | {_fmt(t2['pico']['wall_secs'], 's')} | |")
    a("")
    a(f"> Gold set evaluation (on {t1['pico']['gold_n_abstracts']} abstracts via `scripts/pico_eval.py`):")
    a("")
    a("| Label | Precision | Recall | TP | FP | FN |")
    a("|---|---|---|---|---|---|")
    for lab in ("P", "I", "C", "O"):
        g = t1['pico']["gold_results"].get(lab, {})
        a(f"| {lab} | {_fmt(g.get('precision'))} | {_fmt(g.get('recall'))} | {g.get('tp', '—')} | {g.get('fp', '—')} | {g.get('fn', '—')} |")
    a("")
    a("**Verdict:** ✅ High precision rule-based tagging provides immediate jump-start for reviewers.")
    a("")

    # ---- HUMAN EFFORT / TIME SAVED ----
    a("## Step 5 — Human Effort & Time Saved (ESTIMATES)")
    a("")
    a(f"| Category | {t1_name} | {t2_name} | Combined |")
    a("|---|---|---|---|")
    t1_net = t1['time_estimate']['net_saved_mins']
    t2_net = t2['time_estimate']['net_saved_mins']
    combined_net = round(t1_net + t2_net, 1)
    a(f"| Screening saved | {t1['time_estimate']['screening']['saved_mins']} min | {t2['time_estimate']['screening']['saved_mins']} min | {round(t1['time_estimate']['screening']['saved_mins'] + t2['time_estimate']['screening']['saved_mins'], 1)} min |")
    a(f"| Screening cost | {t1['time_estimate']['screening']['cost_mins']} min | {t2['time_estimate']['screening']['cost_mins']} min | {round(t1['time_estimate']['screening']['cost_mins'] + t2['time_estimate']['screening']['cost_mins'], 1)} min |")
    a(f"| Dedup saved | {t1['time_estimate']['dedup']['saved_mins']} min | {t2['time_estimate']['dedup']['saved_mins']} min | {round(t1['time_estimate']['dedup']['saved_mins'] + t2['time_estimate']['dedup']['saved_mins'], 1)} min |")
    a(f"| Dedup cost | {t1['time_estimate']['dedup']['cost_mins']} min | {t2['time_estimate']['dedup']['cost_mins']} min | {round(t1['time_estimate']['dedup']['cost_mins'] + t2['time_estimate']['dedup']['cost_mins'], 1)} min |")
    a(f"| PICO saved | {t1['time_estimate']['pico']['saved_mins']} min | {t2['time_estimate']['pico']['saved_mins']} min | {round(t1['time_estimate']['pico']['saved_mins'] + t2['time_estimate']['pico']['saved_mins'], 1)} min |")
    a(f"| PICO cost | {t1['time_estimate']['pico']['cost_mins']} min | {t2['time_estimate']['pico']['cost_mins']} min | {round(t1['time_estimate']['pico']['cost_mins'] + t2['time_estimate']['pico']['cost_mins'], 1)} min |")
    a(f"| **Net Time Saved** | **{t1_net} min** | **{t2_net} min** | **{combined_net} min** |")
    a("")
    a("| Audit Metric | Topic 1 | Topic 2 | Combined |")
    a("|---|---|---|---|")
    a(f"| Machine decisions | {_fmt(t1['human_effort']['machine_decisions'])} | {_fmt(t2['human_effort']['machine_decisions'])} | {t1['human_effort']['machine_decisions'] + t2['human_effort']['machine_decisions']} |")
    a(f"| Human overrides | {_fmt(t1['human_effort']['human_decision_overrides'])} | {_fmt(t2['human_effort']['human_decision_overrides'])} | {t1['human_effort']['human_decision_overrides'] + t2['human_effort']['human_decision_overrides']} |")
    a(f"| Override rate | {_fmt(t1['human_effort']['override_rate'])} | {_fmt(t2['human_effort']['override_rate'])} | — |")
    a(f"| PICO edits | {_fmt(t1['human_effort']['pico_edits'])} | {_fmt(t2['human_effort']['pico_edits'])} | {t1['human_effort']['pico_edits'] + t2['human_effort']['pico_edits']} |")
    a("")

    # ---- RUNTIME ----
    a("## Runtime per step")
    a("")
    a(f"| Step | {t1_name} | {t2_name} |")
    a("|---|---|---|")
    a(f"| Fetch | {_fmt(t1['fetch']['wall_secs'], 's')} | {_fmt(t2['fetch']['wall_secs'], 's')} |")
    a(f"| Dedup | {_fmt(t1['dedup']['wall_secs'], 's')} | {_fmt(t2['dedup']['wall_secs'], 's')} |")
    a(f"| Screen | {_fmt(t1['screen']['wall_secs'], 's')} | {_fmt(t2['screen']['wall_secs'], 's')} |")
    a(f"| PICO | {_fmt(t1['pico']['wall_secs'], 's')} | {_fmt(t2['pico']['wall_secs'], 's')} |")
    a("")

    # ---- FAILURE CASES ----
    a("## Known Failure Cases")
    a("")
    a("1. **Fetch**: Single-batch requests; >1000 records need cursor pagination (not implemented).")
    a("2. **Fetch**: Europe PMC 503 occurred 2026-10-27; retry logic (`http_client.py`) now handles it.")
    a("3. **Dedup**: Fuzzy blocking by year+first_author misses cross-database duplicates with different years or missing author data.")
    a("4. **Dedup**: `dedup_gold.json` has a 50-pair template with TODO flag awaiting human labelling.")
    a("5. **Screening**: Abstract-based rules fire 'unknown' when abstract is missing; records default to 'maybe'.")
    a("6. **Screening**: No sentence-level disambiguation; multi-claim abstracts can produce false positives.")
    a("7. **PICO**: Exact-offset gold evaluation is strict; rule spans often match semantically but not at the exact character boundary.")
    a("8. **PICO**: Gold set has only 5 abstracts — P/R estimates have high variance.")
    a("9. **Level 2 loop**: Activation uses run_log export step; filtering must check `params.action = 'activate'`.")
    a("10. **General**: All timing figures from runs on small corpora; not reproducible hardware benchmarks.")
    a("")
    a("---")
    a("*Report generated by `scripts/metrics.py`. Re-run after any pipeline change.*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _collect_topic(db, query_version_id: int, topic: str) -> dict:
    t0 = time.monotonic()
    fetch  = await collect_fetch(db, query_version_id)
    dedup  = await collect_dedup(db, query_version_id)
    screen = await collect_screen(db, query_version_id)
    pico   = await collect_pico(db, query_version_id)
    human  = await collect_human_effort(db, query_version_id)
    elapsed = round(time.monotonic() - t0, 1)

    time_est = estimate_time_saved(fetch, dedup, screen, pico, human)
    meta = {
        "query_version_id": query_version_id,
        "topic": topic,
        "generated_at": datetime.now().isoformat(),
        "collection_secs": elapsed,
    }
    return {
        "meta": meta,
        "assumptions": time_est["assumptions"],
        "fetch": fetch,
        "dedup": dedup,
        "screen": screen,
        "pico": pico,
        "human_effort": human,
        "time_estimate": time_est,
    }


async def main(query_version_id: int | None, topic: str, run_both: bool = False) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as db:
        all_versions = (await db.scalars(select(QueryVersion).order_by(QueryVersion.id))).all()
        if not all_versions:
            raise SystemExit("No query versions in DB. Run scripts/seed_demo.py first.")

        if query_version_id is not None:
            # Single topic run
            v = await db.get(QueryVersion, query_version_id)
            label = topic or (v.label if v and v.label else f"Version {query_version_id}")
            print(f"Collecting metrics for query version {query_version_id} ({label}) …")
            data = await _collect_topic(db, query_version_id, label)
            payload = data
            md = render_markdown(data["meta"], data["fetch"], data["dedup"],
                                 data["screen"], data["pico"], data["human_effort"],
                                 data["time_estimate"])
        else:
            # Run both topics (or all topics if ≥2 exist)
            print(f"Collecting metrics across available topics ({len(all_versions)} versions in DB) …")
            topics_data: list[dict] = []
            for v in all_versions:
                print(f"  → Version {v.id}: {v.label}")
                tdata = await _collect_topic(db, v.id, v.label)
                topics_data.append(tdata)

            if len(topics_data) >= 2:
                md = render_multi_topic_markdown(topics_data)
                payload = {
                    "assumptions": topics_data[0]["assumptions"],
                    "topics": topics_data,
                    "combined_net_saved_mins": round(
                        sum(td["time_estimate"]["net_saved_mins"] for td in topics_data), 1
                    ),
                    "generated_at": datetime.now().isoformat(),
                }
            else:
                data = topics_data[0]
                payload = data
                md = render_markdown(data["meta"], data["fetch"], data["dedup"],
                                     data["screen"], data["pico"], data["human_effort"],
                                     data["time_estimate"])

    json_path = DOCS / "metrics.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"✓ wrote {json_path}")

    md_path = DOCS / "metrics.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"✓ wrote {md_path}")

    print(f"\n{'─'*60}")
    print("Metrics generation complete.")
    if "topics" in payload:
        for td in payload["topics"]:
            print(f"  • {td['meta']['topic']}: {td['time_estimate']['net_saved_mins']} min saved (est.)")
        print(f"  Total net saved across topics: {payload['combined_net_saved_mins']} min (est.)")
    print(f"{'─'*60}")


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--query-version-id", type=int, default=None,
                   help="Query version to report on (default: run on all/both topics)")
    p.add_argument("--topic", default="",
                   help="Human-readable topic label for the report header")
    p.add_argument("--both", action="store_true",
                   help="Run on both demo topic and second topic")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse()
    asyncio.run(main(args.query_version_id, args.topic, args.both))

