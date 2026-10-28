# External API fixtures

- `pubmed_esearch.json` and `pubmed_efetch.xml` — PubMed E-utilities real responses.
- `europepmc_core.json` — Europe PMC core response (a real article; content varies by refresh date).

Run `python scripts/save_api_fixtures.py` to replace all three with fresh live responses.
Europe PMC returned HTTP 503 on 2026-10-27 so a fallback article was used; it was restored
to a live response on 2026-10-28.  Tests in `test_parsers.py` check structural invariants
only (not hard-coded article values) so they survive fixture refreshes.
