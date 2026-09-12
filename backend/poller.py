from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from .models import Transaction
from .rho_client import RhoClient

logger = logging.getLogger("poller")

OnTransactions = Callable[[list[Transaction]], Awaitable[None]]


class TransactionPoller:
    """Owns the poll loop against Rho: cursor tracking, interval timing, and
    a self-trigger hook so a demo action ("send a test transaction now") can
    force an immediate fetch instead of waiting for the next tick.

    This is Max's hour 1-4 piece (loop + cursor + trigger) and hour 9-13
    piece (the backoff/hardening below). `on_transactions` is the seam to the
    rest of the pipeline — wired to graph+scoring in main.py.
    """

    def __init__(
        self,
        client: RhoClient,
        on_transactions: OnTransactions,
        interval_seconds: float = 5.0,
        max_backoff_seconds: float = 60.0,
    ):
        self._client = client
        self._on_transactions = on_transactions
        self._interval = interval_seconds
        self._max_backoff = max_backoff_seconds

        self._cursor: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._trigger = asyncio.Event()
        self._stopped = asyncio.Event()

        # hardening (hour 9-13): back off on empty/errored polls, reset on success
        self._consecutive_empty = 0
        self._consecutive_errors = 0

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run_loop(), name="rho-poller")

    async def stop(self) -> None:
        self._stopped.set()
        self._trigger.set()  # wake the loop so it can exit promptly
        if self._task:
            await self._task
            self._task = None

    def trigger_now(self) -> None:
        """Self-triggered fetch hook: call this right after a demo action
        (e.g. posting a test transaction) to skip the wait for the next tick.
        """
        self._trigger.set()

    async def _run_loop(self) -> None:
        while not self._stopped.is_set():
            await self._poll_once()
            wait = self._current_interval()
            try:
                await asyncio.wait_for(self._trigger.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            finally:
                self._trigger.clear()

    async def _poll_once(self) -> None:
        try:
            transactions, next_cursor = await self._client.fetch_transactions(self._cursor)
        except Exception:
            self._consecutive_errors += 1
            logger.exception("poll failed (consecutive=%d)", self._consecutive_errors)
            return

        self._consecutive_errors = 0
        self._cursor = next_cursor

        if not transactions:
            self._consecutive_empty += 1
            return

        self._consecutive_empty = 0
        logger.info("fetched %d transaction(s)", len(transactions))
        await self._on_transactions(transactions)

    def _current_interval(self) -> float:
        """TODO(Max): tune this curve against real Rho rate limits. For now:
        back off linearly on empty polls, exponentially (capped) on errors.
        """
        if self._consecutive_errors:
            return min(self._interval * (2 ** self._consecutive_errors), self._max_backoff)
        if self._consecutive_empty:
            return min(self._interval + self._consecutive_empty, self._max_backoff)
        return self._interval
