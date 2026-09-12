"""Wires the bus: transactions.new -> graph -> scorer -> transactions.scored.

The broadcaster subscribes to `transactions.scored` in main.py, so this module
knows nothing about HTTP or SSE.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone

from app.bus import TOPIC_TRANSACTIONS_NEW, TOPIC_TRANSACTIONS_SCORED, EventBus
from app.graph.graph import TransactionGraph
from app.models import ScoredTransaction, TransactionEvent
from app.scoring.base import Scorer

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, graph: TransactionGraph, scorer: Scorer, bus: EventBus, *, history_size: int = 1000) -> None:
        self.graph = graph
        self.scorer = scorer
        self.bus = bus
        self.history: deque[ScoredTransaction] = deque(maxlen=history_size)
        self.counters = {"processed": 0, "backfill": 0, "live": 0, "updated": 0, "warn": 0, "alert": 0}
        self.last: ScoredTransaction | None = None
        bus.subscribe(TOPIC_TRANSACTIONS_NEW, self.handle_transaction)

    async def handle_transaction(self, event: TransactionEvent) -> None:
        tx = event.transaction
        # A status change is not a new money movement: re-score against the graph without re-inserting.
        features = self.graph.apply(tx) if event.kind == "new" else self.graph.features_for(tx)
        anomaly = self.scorer.score(tx, features)
        scored = ScoredTransaction(event=event, features=features, anomaly=anomaly, scored_at=datetime.now(timezone.utc))

        self.history.append(scored)
        self.last = scored
        self.counters["processed"] += 1
        self.counters["backfill" if event.backfill else "live"] += 1
        if event.kind == "updated":
            self.counters["updated"] += 1
        if anomaly.level in ("warn", "alert"):
            self.counters[anomaly.level] += 1
            if not event.backfill:
                log.info(
                    "%s %.2f  $%,.2f  %s -> %s  [%s]",
                    anomaly.level.upper(),
                    anomaly.score,
                    tx.amount_major,
                    tx.account_name,
                    tx.counterparty_name,
                    "; ".join(anomaly.reasons),
                )

        await self.bus.publish(TOPIC_TRANSACTIONS_SCORED, scored)

    def recent(self, limit: int = 100, *, flagged_only: bool = False) -> list[ScoredTransaction]:
        items = reversed(self.history)  # newest first
        if flagged_only:
            items = (s for s in items if s.anomaly.level != "normal")
        out: list[ScoredTransaction] = []
        for s in items:
            out.append(s)
            if len(out) >= limit:
                break
        return out
