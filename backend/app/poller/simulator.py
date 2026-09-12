"""Demo sources that publish on the same topic as the real poller.

The Rho sandbox is a frozen set of 72 transactions and every endpoint is GET-only,
so nothing new ever arrives. For a live demo we need to feed the pipeline ourselves:

  - ReplaySource: cycles through the fixture as if it were happening now (frontend dev).
  - make_injected_transaction: builds a Rho-shaped transaction from a small request
    (used by POST /api/demo/inject for the pitch: "trigger a transaction, watch it get flagged").

Downstream (graph, scorer, SSE, UI) cannot tell these apart from real polling except
via TransactionEvent.source.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.bus import TOPIC_TRANSACTIONS_NEW, EventBus
from app.models import Money, Transaction, TransactionEvent

log = logging.getLogger(__name__)

DEFAULT_ACCOUNT_ID = "30000000-0000-4000-8000-000000000006"  # sandbox "Primary Checking"


def load_fixture_transactions(fixtures_dir: Path) -> list[dict[str, Any]]:
    data = json.loads((fixtures_dir / "sandbox_transactions.json").read_text())
    rows = data["transactions"] if isinstance(data, dict) else data
    return sorted(rows, key=lambda r: r["initiated_at"])


def load_fixture_accounts(fixtures_dir: Path) -> list[dict[str, Any]]:
    data = json.loads((fixtures_dir / "sandbox_accounts.json").read_text())
    return data["accounts"] if isinstance(data, dict) else data


class InjectRequest(BaseModel):
    """Body for POST /api/demo/inject. Amount is signed minor units: -4200000 == -$42,000."""

    counterparty_name: str
    amount: int
    account_id: str | None = None
    account_name: str | None = None
    account_type: str | None = None
    transaction_type: str | None = None
    status: str = "pending"
    currency: str = "USD"
    memo: str | None = None
    user_full_name: str | None = None
    card_name: str | None = None
    initiated_at: datetime | None = None


def make_injected_transaction(req: InjectRequest, accounts: list[dict[str, Any]] | None = None) -> Transaction:
    accounts = accounts or []
    account = next((a for a in accounts if a.get("id") == req.account_id), None)
    if account is None and req.account_id is None:
        account = next((a for a in accounts if a.get("account_type") == "checking"), None)
    account_id = req.account_id or (account or {}).get("id") or DEFAULT_ACCOUNT_ID
    now = datetime.now(timezone.utc)
    initiated = req.initiated_at or now
    tx_type = req.transaction_type or ("card_debit" if req.card_name else ("ach_debit" if req.amount < 0 else "ach_credit"))
    return Transaction(
        id=f"inject-{uuid.uuid4().hex[:12]}",
        money_movement_id=f"inject-mm-{uuid.uuid4().hex[:12]}",
        account_id=account_id,
        account_name=req.account_name or (account or {}).get("account_name") or "Injected Account",
        account_type=req.account_type or (account or {}).get("account_type") or "checking",
        transaction_type=tx_type,
        amount=Money(amount=req.amount, currency=req.currency),
        status=req.status,
        initiated_at=initiated,
        posted_at=initiated if req.status in ("settled", "failed") else None,
        user_full_name=req.user_full_name,
        card_name=req.card_name,
        counterparty_name=req.counterparty_name,
        memo=req.memo,
        raw={"injected": True},
    )


class ReplaySource:
    """Replays fixture rows forever with fresh ids and current timestamps."""

    def __init__(self, bus: EventBus, fixtures_dir: Path, interval_seconds: float = 3.0) -> None:
        self.bus = bus
        self.rows = load_fixture_transactions(fixtures_dir)
        self.interval = interval_seconds
        self.emitted = 0

    async def run(self) -> None:
        if not self.rows:
            log.warning("replay: fixture is empty, nothing to do")
            return
        log.info("replay: %d fixture rows, one every %.1fs", len(self.rows), self.interval)
        while True:
            for row in self.rows:
                tx = self._shift(row)
                await self.bus.publish(
                    TOPIC_TRANSACTIONS_NEW, TransactionEvent(kind="new", transaction=tx, backfill=False, source="replay")
                )
                self.emitted += 1
                await asyncio.sleep(self.interval)

    @staticmethod
    def _shift(row: dict[str, Any]) -> Transaction:
        now = datetime.now(timezone.utc).isoformat()
        shifted = dict(row)
        shifted["id"] = f"sim-{uuid.uuid4().hex[:12]}"
        shifted["money_movement_id"] = f"sim-mm-{uuid.uuid4().hex[:12]}"
        shifted["initiated_at"] = now
        shifted["posted_at"] = now if row.get("status") in ("settled", "failed") else None
        return Transaction.from_rho(shifted)
