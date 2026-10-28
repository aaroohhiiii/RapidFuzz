from __future__ import annotations

import re
from typing import Any


def normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    cleaned = cleaned.replace("https://", "").replace("http://", "")
    cleaned = cleaned.replace("dx.doi.org/", "").replace("doi.org/", "")
    return cleaned or None


def normalize_title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    cleaned = re.sub(r"[^\w\s]", " ", cleaned.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def normalize_authors(authors: Any) -> list[str] | None:
    if authors is None:
        return None
    if isinstance(authors, list):
        return [str(item).strip() for item in authors if str(item).strip()]
    if isinstance(authors, str):
        return [part.strip() for part in authors.split(";") if part.strip()]
    return None


def extract_first_author(authors: Any) -> str | None:
    normalized = normalize_authors(authors)
    if not normalized:
        return None
    first = normalized[0].strip()
    return first.split()[-1] if first else None
