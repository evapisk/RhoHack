"""Check a brand-new vendor against the open web (Tavily search).

An impostor invoicing as "Crescent Property Group LLC" usually has no website, no
listings, nothing. That absence sharpens an alert the graph already raised; it is not
evidence on its own, because plenty of real small vendors have no web presence either.

So the rule is deliberately narrow:
  - only live, never-seen, external counterparties are looked up (pipeline.py enforces
    this; the 31 first-seen vendors in a fixture replay would otherwise cost 31 calls
    on every demo reset)
  - nothing found adds VERIFICATION_WEIGHT evidence, and only when the transaction is
    already warn or alert
  - the level never changes: normal stays normal, warn stays warn

Tavily being slow, down, out of credits or unconfigured degrades to "no enrichment",
the same posture as rho_client.py and demo/seed.py:bootstrap.

API contract (docs.tavily.com, Search endpoint):
    POST https://api.tavily.com/search   Authorization: Bearer <key>
    {"query", "search_depth", "max_results"} -> {"results": [{"title", "url", "content", "score"}]}
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.graph.lookalike import normalize_for_match, significant_tokens
from app.models import AnomalyScore

log = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_RESULTS = 5
VERIFICATION_WEIGHT = 0.6


@dataclass(frozen=True)
class VendorVerification:
    name: str
    found: bool
    title: str | None = None
    url: str | None = None
    domain: str | None = None
    snippet: str | None = None
    result_count: int = 0


def _tokens(name: str | None) -> list[str]:
    # "AB Co" has no significant tokens; fall back to every token rather than
    # declaring a vendor unfindable because its name is short.
    return significant_tokens(name) or normalize_for_match(name).split()


def _classify(name: str, tokens: list[str], results: list[object]) -> VendorVerification:
    """Found means one result's title, or its domain, contains every token of the name."""
    for r in results:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "")
        url = str(r.get("url") or "")
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        title_tokens = set(normalize_for_match(title).split())
        host_compact = normalize_for_match(host).replace(" ", "")
        if all(t in title_tokens for t in tokens) or (host_compact and all(t in host_compact for t in tokens)):
            return VendorVerification(
                name=name,
                found=True,
                title=title or None,
                url=url or None,
                domain=host or None,
                snippet=(str(r.get("content") or "")[:200] or None),
                result_count=len(results),
            )
    return VendorVerification(name=name, found=False, result_count=len(results))


async def verify_vendor(
    counterparty_name: str | None,
    *,
    api_key: str,
    client: httpx.AsyncClient,
    timeout: float,
) -> VendorVerification | None:
    """Search the web for the vendor. None means "could not tell", never "not found"."""
    if not api_key.strip():
        log.warning("TAVILY_API_KEY is empty; skipping vendor verification")
        return None
    tokens = _tokens(counterparty_name)
    if not counterparty_name or not tokens:
        log.warning("vendor verification skipped: nothing searchable in %r", counterparty_name)
        return None

    try:
        response = await asyncio.wait_for(
            client.post(
                TAVILY_SEARCH_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": counterparty_name, "search_depth": "basic", "max_results": MAX_RESULTS},
            ),
            timeout,
        )
    except TimeoutError:
        log.warning("vendor verification for %r timed out after %.1fs; skipping", counterparty_name, timeout)
        return None
    except Exception as exc:  # network, TLS, anything: enrichment is optional
        log.warning("vendor verification for %r failed (%s); skipping", counterparty_name, exc)
        return None

    if response.status_code >= 400:
        log.warning("vendor verification for %r got HTTP %d; skipping", counterparty_name, response.status_code)
        return None
    try:
        results = response.json().get("results") or []
    except (ValueError, AttributeError) as exc:
        log.warning("vendor verification for %r returned an unreadable body (%s); skipping", counterparty_name, exc)
        return None
    if not isinstance(results, list):
        log.warning("vendor verification for %r returned non-list results; skipping", counterparty_name)
        return None
    return _classify(counterparty_name, tokens, results)


class VendorVerifier:
    """Owns the HTTP client and a per-name cache.

    The cache lives here rather than in the graph so a demo reset does not clear it:
    the second run of a scenario makes no API call and scores identically to the first.
    Only real answers are cached, so a timeout can recover on the next transaction.
    """

    def __init__(self, api_key: str, *, timeout: float = 3.0, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._cache: dict[str, VendorVerification] = {}

    async def verify(self, counterparty_name: str | None) -> VendorVerification | None:
        key = normalize_for_match(counterparty_name)
        if key in self._cache:
            return self._cache[key]
        result = await verify_vendor(
            counterparty_name, api_key=self._api_key, client=self._client, timeout=self._timeout
        )
        if result is not None:
            self._cache[key] = result
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def apply_vendor_verification(
    anomaly: AnomalyScore,
    verification: VendorVerification | None,
    *,
    scale: float,
    alert: float,
) -> AnomalyScore:
    """Fold a web check into an existing score without ever changing its level."""
    if verification is None:
        return anomaly

    reasons = list(anomaly.reasons)
    components = dict(anomaly.components)
    components["vendor_verification"] = 0.0

    if verification.found:
        where = f" ({verification.domain})" if verification.domain else ""
        reasons.append(f"Web check: found a web presence for '{verification.name}'{where}")
        return anomaly.model_copy(update={"reasons": reasons, "components": components})

    reasons.append(f"Web check: no web presence found for '{verification.name}'")
    evidence = components.get("combined_evidence")
    if anomaly.level == "normal" or evidence is None:
        return anomaly.model_copy(update={"reasons": reasons, "components": components})

    evidence += VERIFICATION_WEIGHT
    score = 1.0 - math.exp(-evidence / scale)
    # Stay inside the current band: a warn nudged past the alert line would be a new
    # alert created by the web check alone.
    ceiling = 1.0 if anomaly.level == "alert" else alert - 0.0001
    score = max(anomaly.score, min(score, ceiling))
    components["vendor_verification"] = VERIFICATION_WEIGHT
    components["combined_evidence"] = round(evidence, 3)
    return anomaly.model_copy(
        update={"score": round(score, 4), "reasons": reasons, "components": components}
    )
