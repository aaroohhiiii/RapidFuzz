from __future__ import annotations

from datetime import date

from app.config import settings
from app.services import http_client
from app.services.normalize import extract_first_author, normalize_doi, normalize_title

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


async def count_hits(query_text: str, date_from: date | None = None, date_to: date | None = None) -> int:
    params = {
        "query": _build_query(query_text, date_from, date_to),
        "format": "json",
        "resultType": "core",
        "pageSize": 1,
    }
    payload = await http_client.get_json(BASE_URL, params)
    return int(payload.get("hitCount", 0))


async def fetch_records(query_text: str, date_from: date | None = None, date_to: date | None = None, limit: int = 250) -> list[dict]:
    ids = await search_ids(query_text, date_from, date_to, limit)
    return await fetch_records_by_ids(ids)


async def search_ids(query_text: str, date_from: date | None = None, date_to: date | None = None, limit: int = 250) -> list[str]:
    """Return Europe PMC identifiers from a lightweight search response."""

    params = {
        "query": _build_query(query_text, date_from, date_to),
        "format": "json",
        "resultType": "lite",
        "pageSize": min(limit, 1000),
    }
    payload = await http_client.get_json(BASE_URL, params)
    return [str(item.get("id") or item.get("pmid") or "") for item in payload.get("resultList", {}).get("result", []) if item.get("id") or item.get("pmid")]


async def fetch_records_by_ids(ids: list[str]) -> list[dict]:
    """Fetch Europe PMC core JSON only for supplied new identifiers."""

    if not ids:
        return []
    params = {
        "query": " OR ".join(f"EXT_ID:{value}" for value in ids),
        "format": "json",
        "resultType": "core",
        "pageSize": min(len(ids), 1000),
    }
    payload = await http_client.get_json(BASE_URL, params)
    return parse_core_response(payload)


def parse_core_response(payload: dict) -> list[dict]:
    results = payload.get("resultList", {}).get("result", [])
    records: list[dict] = []
    for item in results:
        authors = [
            author["fullName"]
            for author in item.get("authorList", {}).get("author", [])
            if author.get("fullName")
        ] or _authors_from_string(item.get("authorString"))
        title = item.get("title")
        abstract = item.get("abstractText")
        pmid = item.get("pmid")
        journal_info = item.get("journalInfo") or {}
        journal = journal_info.get("journal") or {}
        pub_types = item.get("pubTypeList", {}).get("pubType", item.get("pubType") or [])
        mesh_terms = [
            heading.get("descriptorName")
            for heading in item.get("meshHeadingList", {}).get("meshHeading", [])
            if heading.get("descriptorName")
        ]
        records.append(
            {
                "source": "europepmc",
                "source_id": str(item.get("id") or item.get("pmid") or ""),
                "pmid": pmid,
                "doi": normalize_doi(item.get("doi")),
                "title": title,
                "title_clean": normalize_title(title),
                "abstract": abstract,
                "authors": authors,
                "first_author": extract_first_author(authors),
                "year": _coerce_year(item.get("firstPublicationDate")),
                "journal": journal.get("title") or item.get("journalTitle"),
                "volume": journal_info.get("volume") or item.get("journalVolume"),
                "pages": item.get("pageInfo"),
                "language": item.get("language") or None,
                "pub_types": pub_types if isinstance(pub_types, list) else [pub_types],
                "mesh_terms": mesh_terms,
                "raw": item,
            }
        )
    return records


def _build_query(query_text: str, date_from: date | None = None, date_to: date | None = None) -> str:
    base = query_text.strip()
    if date_from is None and date_to is None:
        return base
    start = date_from.isoformat() if date_from else "1900-01-01"
    end = date_to.isoformat() if date_to else "3000-12-31"
    return f"({base}) AND (FIRST_PDATE:[{start}] TO [{end}])"


def _authors_from_string(value: str | None) -> list[str]:
    if not value:
        return []
    separator = ";" if ";" in value else ","
    return [part.strip().rstrip(".") for part in value.split(separator) if part.strip()]


def _coerce_year(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value[:4])
    except (TypeError, ValueError):
        return None
