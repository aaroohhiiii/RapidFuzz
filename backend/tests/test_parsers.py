from __future__ import annotations

import json
from pathlib import Path

from app.services.europepmc import parse_core_response
from app.services.pubmed import parse_efetch_xml, parse_esearch


FIXTURES = Path(__file__).parent / "fixtures"


def test_pubmed_parsers_accept_saved_real_responses():
    search = json.loads((FIXTURES / "pubmed_esearch.json").read_text(encoding="utf-8"))
    total, ids = parse_esearch(search)
    records = parse_efetch_xml((FIXTURES / "pubmed_efetch.xml").read_bytes())

    assert total >= len(ids) > 0
    assert {record["source_id"] for record in records} == set(ids)
    assert all(record["source"] == "pubmed" for record in records)
    assert all(record["title"] and record["title_clean"] for record in records)
    assert any(record["abstract"] for record in records)


def test_europepmc_parser_accepts_saved_real_core_response():
    payload = json.loads((FIXTURES / "europepmc_core.json").read_text(encoding="utf-8"))
    source_results = payload["resultList"]["result"]
    records = parse_core_response(payload)

    assert len(records) == len(source_results) > 0
    assert all(record["source"] == "europepmc" for record in records)
    assert all(record["source_id"] and record["title"] for record in records)
    assert all(isinstance(record["authors"], list) for record in records)
    assert all(isinstance(record["pub_types"], list) for record in records)
    # journal and volume may vary between fixture refreshes; just verify types
    assert records[0]["journal"] is None or isinstance(records[0]["journal"], str)
    assert records[0]["volume"] is None or isinstance(records[0]["volume"], str)
