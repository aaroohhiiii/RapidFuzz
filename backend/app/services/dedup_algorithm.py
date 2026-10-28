"""Deterministic exact/fuzzy deduplication and connected-component grouping."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from rapidfuzz import fuzz

from app.config import settings


@dataclass(frozen=True)
class DedupRecord:
    """The record fields used by the deduplication rules."""

    id: int
    source: str
    doi: str | None = None
    pmid: str | None = None
    title_clean: str | None = None
    abstract: str | None = None
    first_author: str | None = None
    year: int | None = None
    journal: str | None = None
    pages: str | None = None
    pub_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class PairResult:
    """A persistent pair produced by the machine rules."""

    record_a_id: int
    record_b_id: int
    method: str
    score: float
    machine_verdict: str


def find_candidate_pairs(
    records: list[DedupRecord], compare_record_ids: set[int] | None = None
) -> list[PairResult]:
    """Return exact and review-worthy fuzzy pairs in stable record-id order."""

    ordered = sorted(records, key=lambda record: record.id)
    exact = _exact_pairs(ordered, compare_record_ids)
    blocked = _blocked_pair_ids(ordered, compare_record_ids)
    by_id = {record.id: record for record in ordered}
    results: dict[tuple[int, int], PairResult] = dict(exact)

    for pair_ids in sorted(blocked - exact.keys()):
        left, right = (by_id[pair_ids[0]], by_id[pair_ids[1]])
        score = fuzzy_score(left, right)
        if score < settings.DUP_LOW:
            continue
        author_agrees = _same_text(left.first_author, right.first_author)
        year_agrees = left.year is not None and left.year == right.year
        verdict = "duplicate" if score >= settings.DUP_HIGH and author_agrees and year_agrees else "uncertain"
        if verdict == "duplicate" and _is_companion_report(left, right):
            verdict = "uncertain"
        results[pair_ids] = PairResult(*pair_ids, method="fuzzy", score=score, machine_verdict=verdict)

    return [results[pair_ids] for pair_ids in sorted(results)]


def fuzzy_score(left: DedupRecord, right: DedupRecord) -> float:
    """Calculate the configured 0..1 weighted similarity score."""

    title_score = fuzz.token_sort_ratio(left.title_clean or "", right.title_clean or "") / 100
    journal_score = fuzz.token_sort_ratio(left.journal or "", right.journal or "") / 100
    score = (
        title_score * settings.DEDUP_TITLE_WEIGHT
        + float(left.year is not None and left.year == right.year) * settings.DEDUP_YEAR_WEIGHT
        + float(_same_text(left.first_author, right.first_author)) * settings.DEDUP_AUTHOR_WEIGHT
        + journal_score * settings.DEDUP_JOURNAL_WEIGHT
        + float(_same_text(left.pages, right.pages)) * settings.DEDUP_PAGES_WEIGHT
    )
    return round(min(max(score, 0.0), 1.0), 6)


def connected_components(
    record_ids: list[int], effective_duplicate_pairs: list[tuple[int, int]]
) -> list[list[int]]:
    """Build deterministic components, including singleton records."""

    parent = {record_id: record_id for record_id in sorted(set(record_ids))}

    def find(record_id: int) -> int:
        while parent[record_id] != record_id:
            parent[record_id] = parent[parent[record_id]]
            record_id = parent[record_id]
        return record_id

    def union(left_id: int, right_id: int) -> None:
        left_root, right_root = find(left_id), find(right_id)
        if left_root == right_root:
            return
        smaller, larger = sorted((left_root, right_root))
        parent[larger] = smaller

    for left_id, right_id in sorted(effective_duplicate_pairs):
        if left_id in parent and right_id in parent:
            union(left_id, right_id)

    components: dict[int, list[int]] = {}
    for record_id in sorted(parent):
        components.setdefault(find(record_id), []).append(record_id)
    return sorted(components.values(), key=lambda component: component[0])


def choose_representative(records: list[DedupRecord]) -> int:
    """Select one representative using the documented deterministic tie-break."""

    if not records:
        raise ValueError("Cannot choose a representative from an empty group")
    return min(
        records,
        key=lambda record: (
            -int(bool(record.doi)),
            -len(record.abstract or ""),
            -int(record.source == "pubmed"),
            record.id,
        ),
    ).id


def _exact_pairs(
    records: list[DedupRecord], compare_record_ids: set[int] | None = None
) -> dict[tuple[int, int], PairResult]:
    results: dict[tuple[int, int], PairResult] = {}
    for key, method in (("doi", "doi"), ("pmid", "pmid")):
        buckets: dict[str, list[int]] = {}
        for record in records:
            value = getattr(record, key)
            if value:
                buckets.setdefault(value.strip().casefold(), []).append(record.id)
        for ids in buckets.values():
            for left_id, right_id in combinations(sorted(ids), 2):
                if compare_record_ids is not None and not ({left_id, right_id} & compare_record_ids):
                    continue
                pair_ids = (left_id, right_id)
                if pair_ids not in results:  # DOI has priority when both identifiers match.
                    results[pair_ids] = PairResult(
                        left_id,
                        right_id,
                        method=method,
                        score=1.0,
                        machine_verdict="duplicate",
                    )
    return results


def _blocked_pair_ids(
    records: list[DedupRecord], compare_record_ids: set[int] | None = None
) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for left, right in combinations(records, 2):
        if compare_record_ids is not None and not ({left.id, right.id} & compare_record_ids):
            continue
        author_year_block = (
            _same_text(left.first_author, right.first_author)
            and left.year is not None
            and right.year is not None
            and abs(left.year - right.year) <= 1
        )
        left_prefix = (left.title_clean or "")[:30]
        right_prefix = (right.title_clean or "")[:30]
        title_block = bool(left_prefix and left_prefix == right_prefix)
        if author_year_block or title_block:
            pairs.add((left.id, right.id))
    return pairs


def _is_companion_report(left: DedupRecord, right: DedupRecord) -> bool:
    title_similarity = fuzz.token_sort_ratio(left.title_clean or "", right.title_clean or "") / 100
    conference_mismatch = _is_conference(left) != _is_conference(right)
    if title_similarity >= settings.DEDUP_NEAR_TITLE and conference_mismatch:
        return True
    if (
        len(left.abstract or "") >= settings.DEDUP_ABSTRACT_MIN_CHARS
        and len(right.abstract or "") >= settings.DEDUP_ABSTRACT_MIN_CHARS
    ):
        abstract_similarity = fuzz.token_set_ratio(left.abstract or "", right.abstract or "") / 100
        return abstract_similarity < settings.DEDUP_ABSTRACT_LOW
    return False


def _is_conference(record: DedupRecord) -> bool:
    values = " ".join(record.pub_types).casefold()
    return any(marker in values for marker in settings.DEDUP_CONFERENCE_TYPES)


def _same_text(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.strip().casefold() == right.strip().casefold())
