"""Tests for API resilience: missing fields, HTTP 503/429, retries, and rate limits."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import europepmc, pubmed
from app.services.europepmc import parse_core_response
from app.services.pubmed import parse_efetch_xml, parse_esearch


# ---------------------------------------------------------------------------
# Parser robustness: missing / null fields in API responses
# ---------------------------------------------------------------------------

class TestPubMedParserMissingFields:
    def test_no_pmid(self):
        xml = b"""<PubmedArticleSet><PubmedArticle>
          <MedlineCitation><Article>
            <ArticleTitle>No PMID</ArticleTitle>
          </Article></MedlineCitation>
        </PubmedArticle></PubmedArticleSet>"""
        records = parse_efetch_xml(xml)
        assert len(records) == 1
        assert records[0]["pmid"] is None
        assert records[0]["title"] == "No PMID"

    def test_no_abstract(self):
        xml = b"""<PubmedArticleSet><PubmedArticle>
          <MedlineCitation>
            <PMID>123</PMID>
            <Article><ArticleTitle>T</ArticleTitle></Article>
          </MedlineCitation>
        </PubmedArticle></PubmedArticleSet>"""
        records = parse_efetch_xml(xml)
        assert records[0]["abstract"] is None

    def test_no_authors(self):
        xml = b"""<PubmedArticleSet><PubmedArticle>
          <MedlineCitation>
            <PMID>456</PMID>
            <Article>
              <ArticleTitle>No authors</ArticleTitle>
              <AuthorList/>
            </Article>
          </MedlineCitation>
        </PubmedArticle></PubmedArticleSet>"""
        records = parse_efetch_xml(xml)
        assert records[0]["authors"] == []
        assert records[0]["first_author"] is None

    def test_empty_result_set(self):
        xml = b"<PubmedArticleSet></PubmedArticleSet>"
        assert parse_efetch_xml(xml) == []

    def test_structured_abstract(self):
        xml = b"""<PubmedArticleSet><PubmedArticle>
          <MedlineCitation>
            <PMID>789</PMID>
            <Article>
              <ArticleTitle>RCT</ArticleTitle>
              <Abstract>
                <AbstractText Label="BACKGROUND">BG text.</AbstractText>
                <AbstractText Label="RESULTS">Results text.</AbstractText>
              </Abstract>
            </Article>
          </MedlineCitation>
        </PubmedArticle></PubmedArticleSet>"""
        records = parse_efetch_xml(xml)
        abstract = records[0]["abstract"]
        assert "BACKGROUND:" in abstract
        assert "RESULTS:" in abstract

    def test_esearch_missing_count(self):
        count, ids = parse_esearch({"esearchresult": {}})
        assert count == 0
        assert ids == []

    def test_esearch_empty_payload(self):
        count, ids = parse_esearch({})
        assert count == 0


class TestEuropePMCParserMissingFields:
    def test_no_authors(self):
        payload = {"resultList": {"result": [{"id": "1", "title": "T", "abstractText": "A"}]}}
        records = parse_core_response(payload)
        assert records[0]["authors"] == []

    def test_authorstring_fallback(self):
        payload = {"resultList": {"result": [
            {"id": "2", "title": "T", "authorString": "Smith J; Lee K"}
        ]}}
        records = parse_core_response(payload)
        assert "Smith J" in records[0]["authors"]

    def test_null_title(self):
        payload = {"resultList": {"result": [{"id": "3"}]}}
        records = parse_core_response(payload)
        assert records[0]["title"] is None

    def test_pub_types_string(self):
        """pub_types may come as a scalar string from older API versions."""
        payload = {"resultList": {"result": [
            {"id": "4", "pubType": "Journal Article"}
        ]}}
        records = parse_core_response(payload)
        assert isinstance(records[0]["pub_types"], list)

    def test_empty_result_list(self):
        assert parse_core_response({"resultList": {"result": []}}) == []

    def test_completely_empty_payload(self):
        assert parse_core_response({}) == []


# ---------------------------------------------------------------------------
# HTTP retry: 503 → retry → success
# ---------------------------------------------------------------------------

def _make_response(status: int, body: bytes = b"", headers: dict | None = None) -> httpx.Response:
    """Build an httpx.Response with a dummy Request so raise_for_status works."""
    req = httpx.Request("GET", "http://fake")
    return httpx.Response(status, content=body, headers=headers or {}, request=req)


class TestHttpRetry:
    """Tests exercise the real _get retry loop via httpx.AsyncClient patching."""

    @staticmethod
    def _fake_client(responses: list[httpx.Response]):
        """Return a context-manager mock whose .get() pops from responses."""
        call_n = [0]

        async def fake_get(url, **kwargs):
            r = responses[min(call_n[0], len(responses) - 1)]
            call_n[0] += 1
            return r

        cm = AsyncMock()
        cm.get.side_effect = fake_get
        cm.__aenter__ = AsyncMock(return_value=cm)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def test_503_then_success_json(self):
        """get_json retries on 503 and returns the JSON from the 2nd response."""
        import app.services.http_client as hc

        responses = [
            _make_response(503),
            _make_response(200, b'{"hitCount": 42}'),
        ]

        async def scenario():
            with (
                patch("httpx.AsyncClient", return_value=self._fake_client(responses)),
                patch("asyncio.sleep", new=AsyncMock()),
            ):
                result = await hc.get_json("http://fake", {})
            assert result == {"hitCount": 42}

        asyncio.run(scenario())

    def test_timeout_then_success_bytes(self):
        """get_bytes retries once after TimeoutException."""
        import app.services.http_client as hc

        calls = [0]

        async def flaky_get(url, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                raise httpx.TimeoutException("timed out")
            return _make_response(200, b"<data/>")

        cm = AsyncMock()
        cm.get.side_effect = flaky_get
        cm.__aenter__ = AsyncMock(return_value=cm)
        cm.__aexit__ = AsyncMock(return_value=False)

        async def scenario():
            with (
                patch("httpx.AsyncClient", return_value=cm),
                patch("asyncio.sleep", new=AsyncMock()),
            ):
                result = await hc.get_bytes("http://fake", {})
            assert result == b"<data/>"
            assert calls[0] == 2

        asyncio.run(scenario())

    def test_max_retries_exceeded_raises(self):
        """All attempts return 503 → HTTPStatusError propagates."""
        import app.services.http_client as hc
        import app.services.http_client as _hc_mod

        always_503 = _make_response(503)

        async def always_bad(url, **kwargs):
            return always_503

        cm = AsyncMock()
        cm.get.side_effect = always_bad
        cm.__aenter__ = AsyncMock(return_value=cm)
        cm.__aexit__ = AsyncMock(return_value=False)

        async def scenario():
            with (
                patch("httpx.AsyncClient", return_value=cm),
                patch("asyncio.sleep", new=AsyncMock()),
                patch.object(_hc_mod, "_MAX_RETRIES", 1),   # speed the test up
            ):
                with pytest.raises(httpx.HTTPStatusError):
                    await hc.get_json("http://fake", {})

        asyncio.run(scenario())

    def test_retry_after_header_respected(self):
        """_retry_after returns the Retry-After header value as a float."""
        import app.services.http_client as hc

        r429 = _make_response(429, headers={"Retry-After": "7"})
        assert hc._retry_after(r429, 0) == 7.0

    def test_retry_after_falls_back_to_backoff(self):
        """_retry_after falls back to exponential back-off when header is absent."""
        import app.services.http_client as hc

        r503 = _make_response(503)
        wait = hc._retry_after(r503, 0)
        # First attempt: _BACKOFF_BASE * 2^0 = 1.5 (default)
        assert wait > 0

    def test_429_sleep_value_used_in_full_retry(self):
        """End-to-end: 429 with Retry-After:3 causes sleep(3.0) then succeeds."""
        import app.services.http_client as hc

        slept: list[float] = []

        async def fake_sleep(t: float):
            slept.append(t)

        responses = [
            _make_response(429, headers={"Retry-After": "3"}),
            _make_response(200, b'{"ok": true}'),
        ]

        async def scenario():
            with (
                patch("httpx.AsyncClient", return_value=self._fake_client(responses)),
                patch("asyncio.sleep", new=fake_sleep),
            ):
                result = await hc.get_json("http://fake", {})
            assert result == {"ok": True}
            assert slept == [3.0]

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Fetch service: graceful degradation when APIs return empty
# ---------------------------------------------------------------------------

class TestFetchServiceGracefulDegradation:
    def test_pubmed_fetch_returns_empty_list(self, tmp_path, monkeypatch):
        """If fetch_records_by_ids returns [], the fetch run completes without crashing."""
        from tests.db_support import create_test_database
        from app.models import QueryVersion, Record
        from app.services.fetch_service import create_fetch_run, execute_fetch_run

        async def ids(*_a, **_kw):
            return ["1", "2"]

        async def bodies(_ids):
            return []  # API returns nothing (e.g. service degraded)

        async def empty(*_a, **_kw):
            return []

        monkeypatch.setattr(pubmed, "search_ids", ids)
        monkeypatch.setattr(pubmed, "fetch_records_by_ids", bodies)
        monkeypatch.setattr(europepmc, "search_ids", empty)
        monkeypatch.setattr(europepmc, "fetch_records_by_ids", empty)

        async def scenario():
            engine, sessions = await create_test_database(tmp_path / "degrade.sqlite3")
            try:
                async with sessions() as db:
                    v = QueryVersion(query_text="q", status="approved")
                    db.add(v); await db.commit()
                    run = await create_fetch_run(db, v.id, "pubmed")
                    stats = await execute_fetch_run(db, run.id)
                    assert stats["found"] == 2
                    assert stats["new"] == 0
                    assert stats["linked"] == 0
            finally:
                await engine.dispose()

        asyncio.run(scenario())
