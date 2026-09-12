"""Interval poller: fetch -> classify against the cursor -> publish TransactionEvent.

The hook into the rest of the pipeline is `bus.publish(TOPIC_TRANSACTIONS_NEW, event)`.
Nothing here knows about graphs or scoring.

Every start (BACKFILL_ON_START): walk the entire feed oldest -> newest so downstream
stages rebuild their in-memory baseline in chronological order; events carry backfill=True.
Steady state: fetch the newest page, emit anything unseen (or whose status changed),
follow next_page_token only while pages still contain something new.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.bus import TOPIC_TRANSACTIONS_NEW, EventBus
from app.config import Settings
from app.models import Transaction, TransactionEvent
from app.poller.cursor import CursorState
from app.rho_client import RhoAuthError, RhoClient, RhoTransientError

log = logging.getLogger(__name__)

# Rho has no push notification for status changes. Once tick() starts using
# initiated_after to skip re-scanning history (see tick()), a transaction that
# was pending long ago would never be re-fetched by the main scan again. These
# are the statuses sweep_pending() re-checks by id until they reach a terminal
# state.
PENDING_STATUSES = frozenset({"pending", "awaiting_approval"})


class Poller:
    def __init__(
        self,
        client: RhoClient,
        bus: EventBus,
        cursor: CursorState,
        settings: Settings,
        cursor_path: Path,
        *,
        max_pages_per_tick: int = 10,
    ) -> None:
        self.client = client
        self.bus = bus
        self.cursor = cursor
        self.settings = settings
        self.cursor_path = cursor_path
        self.max_pages_per_tick = max_pages_per_tick
        self.ticks = 0
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self._wake = asyncio.Event()

    # -- lifecycle -----------------------------------------------------------

    async def run(self) -> None:
        if self.settings.backfill_on_start:
            await self._guarded(self.backfill)
        while True:
            await self._guarded(self.tick)
            if self.ticks and self.ticks % self.settings.pending_sweep_every_n_ticks == 0:
                await self._guarded(self.sweep_pending)
            await self._wait(self._sleep_seconds())

    async def _guarded(self, step) -> None:
        try:
            await step()
            self.consecutive_failures = 0
            self.last_error = None
        except RhoAuthError as exc:
            self.last_error = f"auth: {exc}"
            log.critical("Rho rejected our token; poller stopping: %s", exc)
            raise
        except RhoTransientError as exc:
            self.consecutive_failures += 1
            self.last_error = str(exc)
            log.warning("transient Rho failure (%d in a row): %s", self.consecutive_failures, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive during the hackathon
            self.consecutive_failures += 1
            self.last_error = repr(exc)
            log.exception("poller step failed")

    def trigger_now(self) -> None:
        """Self-trigger hook: skip the rest of the current interval and poll immediately
        (e.g. right after a demo action). Safe to call from any request handler."""
        self._wake.set()

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except TimeoutError:
            pass
        finally:
            self._wake.clear()

    def _sleep_seconds(self) -> float:
        base = self.settings.poll_interval_seconds
        if self.consecutive_failures:
            return min(60.0, base * (2 ** min(self.consecutive_failures, 5))) + random.uniform(0, 1)
        return base

    # -- steps -----------------------------------------------------------------

    async def backfill(self) -> int:
        """Walk the whole feed oldest -> newest and emit every row as history (backfill=True).

        Runs on every start: graph and scorer state live in memory, so the baseline must be
        rebuilt even when the cursor already knows these ids. The cursor's job is the steady
        state: deciding which rows in a tick are genuinely new or changed.
        """
        log.info("backfill: walking full feed oldest -> newest")
        emitted = 0
        async for row in self.client.iter_transactions(
            page_size=self.settings.page_size, sort_by="initiated_at", order="asc"
        ):
            tx = Transaction.from_rho(row)
            previous = self.cursor.seen.get(tx.id)
            await self.bus.publish(
                TOPIC_TRANSACTIONS_NEW,
                TransactionEvent(kind="new", transaction=tx, backfill=True, source="rho", previous_status=previous),
            )
            self.cursor.record(tx)
            emitted += 1
        self.cursor.backfill_done = True
        self._save()
        log.info("backfill complete: %d transactions", emitted)
        return emitted

    async def tick(self) -> int:
        """One poll. Returns number of events emitted.

        Once we have a high-water mark (set by backfill or a prior tick), ask Rho only
        for rows strictly newer than it (`initiated_after`, walked oldest->newest) instead
        of re-fetching and re-classifying the whole newest page every time. That means a
        status flip on an *older* transaction (e.g. pending -> settled) is no longer seen
        here — sweep_pending() covers that separately, on its own slower cadence, so we're
        not burning the ~60 req/min budget re-scanning history on every tick just to catch
        settlements. Note `initiated_after` is a strict `>`, so even the exact row at the
        high-water mark itself won't come back through this filter either -- that row is
        covered by sweep_pending() too if it's still pending, same as any older one.
        """
        self.ticks += 1
        pending: list[tuple[str, str | None, Transaction]] = []
        token: str | None = None
        pages = 0
        incremental = bool(self.cursor.high_water_initiated_at)
        filters: dict[str, Any] = (
            {"initiated_after": self.cursor.high_water_initiated_at, "sort_by": "initiated_at", "order": "asc"}
            if incremental
            else {}
        )
        while True:
            rows, token = await self.client.list_transactions(
                page_token=token, page_size=self.settings.page_size, **filters
            )
            pages += 1
            if not rows:  # empty page: nothing to do, not an error
                break
            page_new = 0
            for row in rows:
                tx = Transaction.from_rho(row)
                kind, previous = self.cursor.classify(tx)
                if kind is None:
                    continue
                pending.append((kind, previous, tx))
                page_new += 1
            if page_new == 0 or not token or pages >= self.max_pages_per_tick:
                break

        if not incremental:
            # No high-water mark yet: this fell back to the unfiltered, newest-first scan,
            # so flip to chronological order before emitting.
            pending.reverse()
        for kind, previous, tx in pending:
            await self.bus.publish(
                TOPIC_TRANSACTIONS_NEW,
                TransactionEvent(kind=kind, transaction=tx, backfill=False, source="rho", previous_status=previous),
            )
            self.cursor.record(tx)

        self.cursor.last_poll_at = datetime.now(timezone.utc).isoformat()
        self._save()
        if pending:
            log.info("tick %d: %d new/updated transactions (%d page(s))", self.ticks, len(pending), pages)
        else:
            log.debug("tick %d: nothing new (%d page(s))", self.ticks, pages)
        return len(pending)

    async def sweep_pending(self) -> int:
        """Re-check every transaction we last saw as pending/awaiting_approval, by id,
        so a settlement on something older than the high-water mark still surfaces as an
        `updated` event. Bounded by however many transactions are actually in flight, not
        by total history, and resilient to a single lookup failing.
        """
        pending_ids = [tid for tid, status in self.cursor.seen.items() if status in PENDING_STATUSES]
        if not pending_ids:
            return 0

        emitted = 0
        for tid in pending_ids:
            try:
                row = await self.client.get_transaction(tid)
            except RhoTransientError as exc:
                log.warning("settlement sweep: could not refresh %s: %s", tid, exc)
                continue
            tx = Transaction.from_rho(row)
            kind, previous = self.cursor.classify(tx)
            if kind is None:
                continue
            await self.bus.publish(
                TOPIC_TRANSACTIONS_NEW,
                TransactionEvent(kind=kind, transaction=tx, backfill=False, source="rho", previous_status=previous),
            )
            self.cursor.record(tx)
            emitted += 1

        if emitted:
            self._save()
        log.info("settlement sweep: checked %d pending, %d changed", len(pending_ids), emitted)
        return emitted

    def _save(self) -> None:
        try:
            self.cursor.save(self.cursor_path)
        except OSError as exc:
            log.warning("could not persist cursor to %s: %s", self.cursor_path, exc)

    def status(self) -> dict:
        return {
            "ticks": self.ticks,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            **self.cursor.summary(),
        }
