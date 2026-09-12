"""Server-Sent Events fan-out.

One asyncio.Queue per connected browser. `publish` is subscribed to the
`transactions.scored` bus topic; `stream` is what GET /api/stream returns.

Wire format (browser EventSource):
    event: transaction
    data: <ScoredTransaction JSON>

    event: heartbeat
    data: {"ts": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import Request

from app.models import ScoredTransaction

log = logging.getLogger(__name__)


class SSEBroadcaster:
    def __init__(self, *, max_queue: int = 500, heartbeat_seconds: float = 15.0) -> None:
        self._clients: set[asyncio.Queue[tuple[str, str]]] = set()
        self.max_queue = max_queue
        self.heartbeat_seconds = heartbeat_seconds
        self.dropped = 0
        self.sent = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def subscribe(self) -> asyncio.Queue[tuple[str, str]]:
        q: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=self.max_queue)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[tuple[str, str]]) -> None:
        self._clients.discard(q)

    async def publish(self, item: ScoredTransaction) -> None:
        if not self._clients:
            return
        payload = item.model_dump_json()
        for q in list(self._clients):
            try:
                q.put_nowait(("transaction", payload))
                self.sent += 1
            except asyncio.QueueFull:
                self.dropped += 1  # slow consumer; drop rather than stall the pipeline

    async def publish_control(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """Send a non-transaction frame (reset, scenario) to every connected client.

        The feed dedups rows by (id, status) and the fixture reuses real Rho ids, so a
        replay after a reset would be silently swallowed by an already-open browser.
        A reset frame tells clients to flush first. Additive and safe: EventSource
        ignores events for which no listener is registered.
        """
        if not self._clients:
            return
        data = json.dumps(payload or {})
        for q in list(self._clients):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                self.dropped += 1

    async def stream(self, request: Request) -> AsyncIterator[str]:
        q = self.subscribe()
        log.info("SSE client connected (%d total)", self.client_count)
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=self.heartbeat_seconds)
                except TimeoutError:
                    ts = datetime.now(timezone.utc).isoformat()
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': ts})}\n\n"
                    continue
                yield f"event: {event}\ndata: {data}\n\n"
        finally:
            self.unsubscribe(q)
            log.info("SSE client disconnected (%d total)", self.client_count)
