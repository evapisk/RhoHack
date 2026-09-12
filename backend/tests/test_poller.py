import asyncio
from pathlib import Path

from app.bus import TOPIC_TRANSACTIONS_NEW, EventBus
from app.config import Settings
from app.poller.cursor import CursorState
from app.poller.poller import Poller
from app.poller.simulator import load_fixture_transactions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class FakeRhoClient:
    """Mimics the real sandbox (see app/rho_client.py's docstring): newest-first by
    default, and actually honors the `status` / `initiated_after` / `sort_by=initiated_at`
    / `order` filters rather than ignoring them, so tests exercise the same filtering
    behavior tick()/sweep_pending() rely on against the real API.
    """

    def __init__(self, rows, page_size: int = 50):
        self.rows = list(rows)
        self.page_size = page_size
        self.calls = 0
        self.filters_seen: list[dict] = []

    def _filtered_sorted(self, **filters):
        rows = self.rows
        status = filters.get("status")
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        initiated_after = filters.get("initiated_after")
        if initiated_after is not None:
            rows = [r for r in rows if r["initiated_at"] > initiated_after]
        ascending = filters.get("order") == "asc"
        return sorted(rows, key=lambda r: r["initiated_at"], reverse=not ascending)

    async def list_transactions(self, *, page_token=None, page_size=100, **filters):
        self.calls += 1
        self.filters_seen.append(dict(filters))
        rows = self._filtered_sorted(**filters)
        start = int(page_token or 0)
        page = rows[start : start + self.page_size]
        next_token = str(start + self.page_size) if start + self.page_size < len(rows) else None
        return page, next_token

    async def iter_transactions(self, *, page_size=100, max_pages=None, **filters):
        for row in self._filtered_sorted(**{**filters, "order": "asc"}):
            yield row

    async def get_transaction(self, transaction_id: str):
        self.calls += 1
        return next(r for r in self.rows if r["id"] == transaction_id)


def make_poller(tmp_path, client, bus) -> Poller:
    settings = Settings(rho_api_key="test", poll_interval_seconds=0.01, state_dir=tmp_path)
    return Poller(client, bus, CursorState(), settings, tmp_path / "cursor.json")


def collector(store):
    async def handler(event):
        store.append(event)

    return handler


async def test_tick_emits_unseen_rows_oldest_first_then_nothing(tmp_path):
    rows = load_fixture_transactions(FIXTURES)
    bus, seen = EventBus(), []
    bus.subscribe(TOPIC_TRANSACTIONS_NEW, collector(seen))
    client = FakeRhoClient(rows, page_size=50)
    poller = make_poller(tmp_path, client, bus)

    assert await poller.tick() == 72
    assert client.calls == 2  # followed next_page_token while pages had new rows
    stamps = [e.transaction.initiated_at for e in seen]
    assert stamps == sorted(stamps)  # chronological despite newest-first pages
    assert all(e.kind == "new" and not e.backfill and e.source == "rho" for e in seen)

    assert await poller.tick() == 0  # everything known: stops after the first page
    assert client.calls == 3
    assert (tmp_path / "cursor.json").exists()


async def test_incremental_tick_only_asks_for_rows_after_the_high_water_mark(tmp_path):
    """Once a high-water mark exists, tick() must ask Rho for initiated_after=<mark>
    instead of re-fetching (and re-classifying) the whole newest page every time -- the
    hardening step called out for hour 9-13."""
    rows = load_fixture_transactions(FIXTURES)
    client, bus = FakeRhoClient(rows), EventBus()
    poller = make_poller(tmp_path, client, bus)
    await poller.tick()  # first tick: no high-water yet, unfiltered fallback

    filters_before = len(client.filters_seen)
    assert await poller.tick() == 0
    new_filters = client.filters_seen[filters_before:]
    assert new_filters and all(f.get("initiated_after") == poller.cursor.high_water_initiated_at for f in new_filters)
    assert new_filters[0].get("order") == "asc"


async def test_sweep_pending_catches_a_settlement_the_incremental_tick_missed(tmp_path):
    """The whole reason sweep_pending exists: an older transaction settles, initiated_after
    means the regular tick will never see it again, so the sweep has to check it by id."""
    rows = load_fixture_transactions(FIXTURES)
    client, bus, seen = FakeRhoClient(rows), EventBus(), []
    bus.subscribe(TOPIC_TRANSACTIONS_NEW, collector(seen))
    poller = make_poller(tmp_path, client, bus)
    await poller.tick()
    seen.clear()

    older_pending = next(
        r for r in client.rows if r["status"] == "pending" and r["initiated_at"] != poller.cursor.high_water_initiated_at
    )
    older_pending["status"] = "settled"

    # The incremental tick genuinely can't see this -- it's strictly older than the mark.
    assert await poller.tick() == 0

    assert await poller.sweep_pending() == 1
    assert seen[0].kind == "updated"
    assert seen[0].previous_status == "pending"
    assert seen[0].transaction.id == older_pending["id"]
    assert poller.cursor.seen[older_pending["id"]] == "settled"


async def test_sweep_pending_is_a_no_op_with_nothing_in_flight(tmp_path):
    poller = make_poller(tmp_path, FakeRhoClient([]), EventBus())
    assert await poller.sweep_pending() == 0


async def test_run_schedules_a_sweep_every_n_ticks(tmp_path):
    rows = load_fixture_transactions(FIXTURES)
    client, bus = FakeRhoClient(rows), EventBus()
    settings = Settings(rho_api_key="test", poll_interval_seconds=0.01, state_dir=tmp_path, pending_sweep_every_n_ticks=2)
    poller = Poller(client, bus, CursorState(), settings, tmp_path / "cursor.json")

    sweeps = []
    poller.sweep_pending = lambda: sweeps.append(1) or asyncio.sleep(0, result=0)

    run_task = asyncio.create_task(poller.run())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(sweeps) >= 1:
            break
    run_task.cancel()
    await asyncio.gather(run_task, return_exceptions=True)

    assert sweeps  # fired at least once by tick 2
    assert poller.ticks >= 2


async def test_backfill_emits_everything_as_history(tmp_path):
    rows = load_fixture_transactions(FIXTURES)
    bus, seen = EventBus(), []
    bus.subscribe(TOPIC_TRANSACTIONS_NEW, collector(seen))
    poller = make_poller(tmp_path, FakeRhoClient(rows), bus)

    assert await poller.backfill() == 72
    assert all(e.backfill for e in seen)
    assert poller.cursor.backfill_done and poller.cursor.seen_count == 72
    assert await poller.tick() == 0  # backfill primed the cursor


async def test_trigger_now_wakes_the_loop_early(tmp_path):
    poller = make_poller(tmp_path, FakeRhoClient([]), EventBus())
    poller.trigger_now()
    await asyncio.wait_for(poller._wait(10), timeout=1)  # would take 10s without the trigger
