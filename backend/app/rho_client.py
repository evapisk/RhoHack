"""Async client for the Rho API (sandbox or production).

Verified against the sandbox (Sept 2026):
  GET /transactions?page_size=1..100&page_token=...  -> {"page": {"next_page_token": str|null}, "transactions": [...]}
  Newest-first by default. Filters honored: status, transaction_type, posted_after, initiated_after,
  sort_by=initiated_at&order=asc. Unknown params are silently ignored.
  GET /accounts -> {"accounts": [...]}
  Rate limit ~60 req/min per token; 429 carries Retry-After.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, AsyncIterator

import httpx

log = logging.getLogger(__name__)


class RhoError(Exception):
    """Base class for Rho client errors."""


class RhoAuthError(RhoError):
    """401/403 - token missing, expired or lacking scope. Not retryable."""


class RhoTransientError(RhoError):
    """429 / 5xx / network failure after retries. Safe to retry later."""


class RhoClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 20.0,
        max_attempts: int = 4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RhoClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- low level -----------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                response = await self._client.get(path, params=params)
            except httpx.TransportError as exc:
                last_error = exc
                await asyncio.sleep(self._backoff(attempt))
                continue

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                delay = retry_after if retry_after and retry_after > 0 else self._backoff(attempt)
                log.warning("Rho 429 on %s; sleeping %.1fs", path, delay)
                await asyncio.sleep(delay)
                last_error = RhoTransientError("rate limited")
                continue
            if response.status_code in (401, 403):
                raise RhoAuthError(f"{response.status_code} from Rho: {response.text[:200]}")
            if response.status_code >= 500:
                last_error = RhoTransientError(f"{response.status_code} from Rho")
                await asyncio.sleep(self._backoff(attempt))
                continue
            if response.status_code >= 400:
                raise RhoError(f"{response.status_code} from Rho on {path}: {response.text[:300]}")
            return response.json()

        raise RhoTransientError(f"giving up on {path} after {self._max_attempts} attempts: {last_error}")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 0.5 * (2**attempt)) + random.uniform(0, 0.5)

    # -- endpoints -----------------------------------------------------------

    async def list_transactions(
        self,
        *,
        page_token: str | None = None,
        page_size: int = 100,
        **filters: Any,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """One page. Returns (rows, next_page_token). Extra kwargs become query filters."""
        params: dict[str, Any] = {"page_size": page_size}
        params.update({k: v for k, v in filters.items() if v is not None})
        if page_token:
            params["page_token"] = page_token
        data = await self._get("/transactions", params)
        rows = data.get("transactions") or []
        next_token = (data.get("page") or {}).get("next_page_token")
        return rows, next_token

    async def iter_transactions(
        self, *, page_size: int = 100, max_pages: int | None = None, **filters: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page with the same filters (Rho page tokens are filter-bound)."""
        token: str | None = None
        pages = 0
        while True:
            rows, token = await self.list_transactions(page_token=token, page_size=page_size, **filters)
            pages += 1
            for row in rows:
                yield row
            if not token or not rows or (max_pages is not None and pages >= max_pages):
                return

    async def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        return await self._get(f"/transactions/{transaction_id}")

    async def list_accounts(self) -> list[dict[str, Any]]:
        data = await self._get("/accounts", {"page_size": 100})
        return data.get("accounts") or []


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
