"""Save small, real PubMed and Europe PMC responses for parser tests.

Run from backend/: python scripts/save_api_fixtures.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx


QUERY = '"semaglutide"[Title] AND randomized controlled trial[Publication Type]'
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def get_with_retries(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    response = None
    for attempt in range(4):
        response = client.get(url, **kwargs)
        if response.status_code < 500:
            response.raise_for_status()
            return response
        time.sleep(1 + attempt)
    assert response is not None
    response.raise_for_status()
    return response


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        search = get_with_retries(
            client,
            f"{PUBMED}/esearch.fcgi",
            params={"db": "pubmed", "term": QUERY, "retmode": "json", "retmax": 2},
        )
        search_payload = search.json()
        (FIXTURES / "pubmed_esearch.json").write_text(
            json.dumps(search_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        ids = search_payload["esearchresult"]["idlist"]
        fetched = get_with_retries(
            client,
            f"{PUBMED}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml"},
        )
        (FIXTURES / "pubmed_efetch.xml").write_bytes(fetched.content)

        epmc = get_with_retries(
            client,
            EUROPE_PMC,
            params={"query": "semaglutide AND OPEN_ACCESS:Y", "format": "json", "resultType": "core", "pageSize": 2},
        )
        (FIXTURES / "europepmc_core.json").write_text(
            json.dumps(epmc.json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    print(f"Saved PubMed esearch + efetch and Europe PMC core fixtures to {FIXTURES}")


if __name__ == "__main__":
    main()
