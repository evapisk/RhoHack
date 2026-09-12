"""Tiny in-process pub/sub. Keeps poller, graph/scorer and SSE loosely coupled.

Handlers for a topic run sequentially in subscription order, so the pipeline
(graph -> scorer) always finishes before the broadcaster sees the result.
A failing handler is logged and does not stop the others.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[None]]

TOPIC_TRANSACTIONS_NEW = "transactions.new"  # payload: TransactionEvent
TOPIC_TRANSACTIONS_SCORED = "transactions.scored"  # payload: ScoredTransaction


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self.published: dict[str, int] = defaultdict(int)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)

    async def publish(self, topic: str, payload: Any) -> None:
        self.published[topic] += 1
        for handler in list(self._handlers.get(topic, [])):
            try:
                await handler(payload)
            except Exception:  # noqa: BLE001 - one bad subscriber must not kill the feed
                log.exception("handler %s failed on topic %s", getattr(handler, "__qualname__", handler), topic)
