from app.services.dedup_algorithm import (
    DedupRecord,
    choose_representative,
    connected_components,
    find_candidate_pairs,
)


def record(record_id: int, **overrides) -> DedupRecord:
    values = {
        "id": record_id,
        "source": "pubmed",
        "title_clean": "semaglutide treatment for adults with obesity randomized trial",
        "abstract": "A" * 200,
        "first_author": "smith",
        "year": 2024,
        "journal": "obesity",
        "pages": "10-20",
    }
    values.update(overrides)
    return DedupRecord(**values)


def test_exact_doi_has_priority_and_exact_pmid_crosses_sources():
    pairs = find_candidate_pairs([
        record(1, doi="10.1/test", pmid="100"),
        record(2, source="europepmc", doi="10.1/test", pmid="100"),
        record(3, source="europepmc", title_clean="different", pmid="200"),
        record(4, source="pubmed", title_clean="another", pmid="200"),
    ])

    assert [(pair.record_a_id, pair.record_b_id, pair.method) for pair in pairs] == [
        (1, 2, "doi"),
        (3, 4, "pmid"),
    ]
    assert all(pair.machine_verdict == "duplicate" for pair in pairs)


def test_matching_title_year_author_without_doi_is_duplicate():
    pair = find_candidate_pairs([record(1), record(2, journal=None, pages=None)])[0]
    assert pair.method == "fuzzy"
    assert pair.score >= 0.93
    assert pair.machine_verdict == "duplicate"


def test_conference_companion_report_is_uncertain_and_does_not_merge():
    pair = find_candidate_pairs([
        record(1, pub_types=("Journal Article",)),
        record(2, pub_types=("Meeting Abstract",)),
    ])[0]

    assert pair.machine_verdict == "uncertain"
    assert connected_components([1, 2], []) == [[1], [2]]


def test_components_bridge_groups_and_representative_tie_break_is_stable():
    assert connected_components([1, 2, 3, 4], [(1, 2), (3, 4), (2, 3)]) == [[1, 2, 3, 4]]
    candidates = [
        record(3, source="europepmc", doi="10.1/a", abstract="longer abstract"),
        record(2, source="pubmed", doi="10.1/b", abstract="longer abstract"),
        record(1, source="pubmed", doi=None, abstract="x" * 1000),
    ]
    assert choose_representative(candidates) == 2
    assert choose_representative(list(reversed(candidates))) == 2
