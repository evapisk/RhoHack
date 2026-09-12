from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from .models import ScoredTransaction


class EventBroadcaster:
    """Fan-out of scored transactions to every connected SSE client.

    This is the hour 7-9 merge point: poller -> graph -> scorer feeds in here
    via `publish` (see main.handle_transactions), and each connected client
    gets its own queue via `subscribe` so one slow client can't block the
    others or the pipeline.
    """

    def __init__(self):
        self._subscribers: set[asyncio.Queue[ScoredTransaction]] = set()

    def subscribe(self) -> "asyncio.Queue[ScoredTransaction]":
        queue: "asyncio.Queue[ScoredTransaction]" = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[ScoredTransaction]") -> None:
        self._subscribers.discard(queue)

    async def publish(self, item: ScoredTransaction) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                # slow client: drop the oldest item rather than block the pipeline
                _ = queue.get_nowait()
                queue.put_nowait(item)

    async def sse_stream(self) -> AsyncIterator[str]:
        """Yields raw JSON strings. EventSourceResponse (see main.py) adds
        its own `data: ...\\n\\n` framing — don't add it here too, or clients
        get double-framed, unparseable events."""
        queue = self.subscribe()
        try:
            while True:
                item = await queue.get()
                yield json.dumps(item.to_dict())
        finally:
            self.unsubscribe(queue)
