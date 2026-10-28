from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

from app.config import settings
from app.services import http_client
from app.services.normalize import extract_first_author, normalize_doi, normalize_title


BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


async def count_hits(query_text: str, date_from: date | None = None, date_to: date | None = None) -> int:
    params = {
        "db": "pubmed",
        "term": _build_query(query_text, date_from, date_to),
        "retmode": "json",
        "retmax": 0,
        "sort": "date",
        "tool": settings.NCBI_TOOL,
        "email": settings.NCBI_EMAIL,
    }
    if settings.NCBI_API_KEY:
        params["api_key"] = settings.NCBI_API_KEY
    payload = await http_client.get_json(f"{BASE_URL}/esearch.fcgi", params)
    count, _ = parse_esearch(payload)
    return count


async def fetch_records(query_text: str, date_from: date | None = None, date_to: date | None = None, limit: int = 250) -> list[dict]:
    ids = await search_ids(query_text, date_from, date_to, limit)
    return await fetch_records_by_ids(ids)


async def search_ids(query_text: str, date_from: date | None = None, date_to: date | None = None, limit: int = 250) -> list[str]:
    """Return PubMed identifiers without fetching record bodies."""

    params = {
        "db": "pubmed",
        "term": _build_query(query_text, date_from, date_to),
        "retmode": "json",
        "retmax": min(limit, 1000),
        "sort": "date",
        "tool": settings.NCBI_TOOL,
        "email": settings.NCBI_EMAIL,
    }
    if settings.NCBI_API_KEY:
        params["api_key"] = settings.NCBI_API_KEY
    payload = await http_client.get_json(f"{BASE_URL}/esearch.fcgi", params)
    return [str(value) for value in payload.get("esearchresult", {}).get("idlist", [])]


async def fetch_records_by_ids(ids: list[str]) -> list[dict]:
    """Fetch full PubMed XML only for the supplied new identifiers."""

    if not ids:
        return []
    content = await http_client.get_bytes(
        f"{BASE_URL}/efetch.fcgi",
        {
            "db": "pubmed",
            "id": ",".join(ids),
            "rettype": "abstract",
            "retmode": "xml",
            "tool": settings.NCBI_TOOL,
            "email": settings.NCBI_EMAIL,
        },
    )
    return parse_efetch_xml(content)


def parse_esearch(payload: dict) -> tuple[int, list[str]]:
    result = payload.get("esearchresult", {})
    return int(result.get("count", 0)), [str(value) for value in result.get("idlist", [])]


def parse_efetch_xml(content: bytes | str) -> list[dict]:
    root = ET.fromstring(content)
    records: list[dict] = []
    for article in root.findall("./PubmedArticle"):
        medline = article.find("MedlineCitation")
        if medline is None:
            continue
        title_el = medline.find("Article/ArticleTitle")
        abstract_el = medline.find("Article/Abstract")
        pmid_el = medline.find("PMID")
        authors = []
        for author in medline.findall("Article/AuthorList/Author"):
            last = author.findtext("LastName") or ""
            fore = author.findtext("ForeName") or ""
            if last or fore:
                authors.append(f"{last} {fore}".strip())
        title = _element_text(title_el)
        abstract_parts = []
        if abstract_el is not None:
            for part in abstract_el.findall("AbstractText"):
                value = _element_text(part)
                if value:
                    label = part.attrib.get("Label")
                    abstract_parts.append(f"{label}: {value}" if label else value)
        abstract_text = "\n".join(abstract_parts) or None
        pub_types = [node.text for node in medline.findall("Article/PublicationTypeList/PublicationType") if node.text]
        mesh_terms = [node.text for node in medline.findall("MeshHeadingList/MeshHeading/DescriptorName") if node.text]
        doi = None
        for ele in medline.findall("Article/ELocationID"):
            if ele.attrib.get("EIdType") == "doi":
                doi = ele.text
                break
        article_json = {
            "source": "pubmed",
            "source_id": pmid_el.text if pmid_el is not None and pmid_el.text else None,
            "pmid": pmid_el.text if pmid_el is not None and pmid_el.text else None,
            "doi": normalize_doi(doi),
            "title": title,
            "title_clean": normalize_title(title),
            "abstract": abstract_text,
            "authors": authors,
            "first_author": extract_first_author(authors),
            "year": _extract_year(medline),
            "journal": _extract_text(medline, "Article/Journal/Title"),
            "volume": _extract_text(medline, "Article/Journal/JournalIssue/Volume"),
            "pages": _extract_text(medline, "Article/Pagination/MedlinePgn"),
            "language": _extract_text(medline, "Article/Language"),
            "pub_types": pub_types,
            "mesh_terms": mesh_terms,
            "raw": {"pmid": pmid_el.text if pmid_el is not None else None},
        }
        records.append(article_json)
    return records


def _build_query(query_text: str, date_from: date | None = None, date_to: date | None = None) -> str:
    base = query_text.strip()
    if date_from is None and date_to is None:
        return base
    start = date_from.isoformat() if date_from else "1900"
    end = date_to.isoformat() if date_to else "3000"
    return f"({base}) AND ({start}:{end}[dp])"


def _extract_year(medline: ET.Element) -> int | None:
    date_el = medline.find("Article/Journal/JournalIssue/PubDate/Year")
    if date_el is not None and date_el.text and date_el.text.isdigit():
        return int(date_el.text)
    return None


def _extract_text(element: ET.Element, path: str) -> str | None:
    target = element.find(path)
    return _element_text(target)


def _element_text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None
