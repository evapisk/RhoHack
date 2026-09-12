from __future__ import annotations

import itertools
import random
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import settings
from .models import Transaction


class RhoClient:
    """Thin wrapper around the Rho sandbox transactions API.

    This is Max's hour 0-1/1-4 piece. Until the sandbox key and real endpoint
    shape are confirmed, use `MockRhoClient` below so the rest of the
    pipeline (graph, scoring, feed) can be built and tested right now without
    waiting on it.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.rho_api_key
        self.base_url = base_url or settings.rho_base_url
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=10.0,
        )

    async def fetch_transactions(self, cursor: Optional[str]) -> tuple[list[Transaction], Optional[str]]:
        """Fetch one page of transactions newer than `cursor`.

        Returns (transactions, next_cursor). `next_cursor` is what the poller
        hands back in on the next call — an unchanged cursor with an empty
        list means "nothing new yet".

        TODO(Max): swap in the real Rho sandbox endpoint/params once we've
        read the docs. Keep the cursor semantics — TransactionPoller depends
        on them for its interval/backoff logic.
        """
        resp = await self._client.get(
            "/transactions",
            params={"cursor": cursor} if cursor else {},
        )
        resp.raise_for_status()
        payload = resp.json()
        transactions = [Transaction.from_rho_payload(tx) for tx in payload.get("data", [])]
        next_cursor = payload.get("next_cursor", cursor)
        return transactions, next_cursor

    async def aclose(self) -> None:
        await self._client.aclose()


class MockRhoClient(RhoClient):
    """Generates fake transactions so graph/scoring/feed can be built and
    demoed before the real Rho integration lands. Controlled by
    config.USE_MOCK_RHO — flip that off once sandbox keys work end to end.
    """

    _MERCHANTS = ["Uber", "AWS", "Staples", "Delta", "Figma", "Notion", "WeWork", "Sketchy LLC"]
    _ACCOUNTS = ["acct_001", "acct_002", "acct_003"]

    def __init__(self):  # intentionally skip RhoClient.__init__: no real HTTP client needed
        self._counter = itertools.count()

    async def fetch_transactions(self, cursor: Optional[str]) -> tuple[list[Transaction], Optional[str]]:
        n = random.choice([0, 0, 1, 1, 2, 3])
        txs = []
        for _ in range(n):
            is_outlier = random.random() < 0.1
            amount = round(random.uniform(5, 300) if not is_outlier else random.uniform(2000, 9000), 2)
            txs.append(
                Transaction(
                    id=str(uuid.uuid4()),
                    timestamp=datetime.now(timezone.utc),
                    amount=amount,
                    currency="USD",
                    account_id=random.choice(self._ACCOUNTS),
                    counterparty=random.choice(self._MERCHANTS),
                )
            )
        next_cursor = f"mock-{next(self._counter)}"
        return txs, next_cursor

    async def aclose(self) -> None:
        pass
