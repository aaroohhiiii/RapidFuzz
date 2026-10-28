"""Shared resilient HTTP client for external API calls.

Wraps httpx with:
- Exponential back-off retry on 429, 503, and transient timeouts.
- Respects Retry-After header when present.
- Configurable via env: HTTP_MAX_RETRIES, HTTP_BACKOFF_BASE, HTTP_TIMEOUT.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "4"))
_BACKOFF_BASE = float(os.getenv("HTTP_BACKOFF_BASE", "1.5"))  # seconds
_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30.0"))
_RETRY_STATUSES = {429, 500, 502, 503, 504}


async def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET → parse JSON with retry."""
    response = await _get(url, params)
    return response.json()


async def get_bytes(url: str, params: dict[str, Any] | None = None) -> bytes:
    """GET → raw bytes with retry."""
    response = await _get(url, params)
    return response.content


async def _get(url: str, params: dict[str, Any] | None) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(url, params=params)
            if response.status_code not in _RETRY_STATUSES:
                response.raise_for_status()
                return response
            # Retryable HTTP status
            wait = _retry_after(response, attempt)
            logger.warning(
                "HTTP %s from %s (attempt %d/%d); retrying in %.1fs",
                response.status_code, url, attempt + 1, _MAX_RETRIES, wait,
            )
            if attempt == _MAX_RETRIES:
                response.raise_for_status()
            await asyncio.sleep(wait)
        except httpx.TimeoutException as exc:
            last_exc = exc
            wait = _backoff(attempt)
            logger.warning(
                "Timeout on %s (attempt %d/%d); retrying in %.1fs",
                url, attempt + 1, _MAX_RETRIES, wait,
            )
            if attempt == _MAX_RETRIES:
                raise
            await asyncio.sleep(wait)
    # Should never reach here, but keeps mypy happy
    raise last_exc or RuntimeError("Retry loop exhausted without raising")


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """Parse Retry-After header or fall back to exponential back-off."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(0.5, float(header))
        except ValueError:
            pass
    return _backoff(attempt)


def _backoff(attempt: int) -> float:
    return _BACKOFF_BASE * (2 ** attempt)
