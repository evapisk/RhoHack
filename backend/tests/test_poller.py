import asyncio
from pathlib import Path

from app.bus import TOPIC_TRANSACTIONS_NEW, EventBus
from app.config import Settings
from app.poller.cursor import CursorState
from app.poller.poller import Poller
from app.poller.simulator import load_fixture_transactions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class FakeRhoClient:
    """Serves the fixture newest-first in pages, like the real sandbox."""

    def __init__(self, rows, page_size: int = 50):
        self.rows = sorted(rows, key=lambda r: r["initiated_at"], reverse=True)
        self.page_size = page_size
        self.calls = 0

    async def list_transactions(self, *, page_token=None, page_size=100, **filters):
        self.calls += 1
        start = int(page_token or 0)
        page = self.rows[start : start + self.page_size]
        next_token = str(start + self.page_size) if start + self.page_size < len(self.rows) else None
        return page, next_token

    async def iter_transactions(self, *, page_size=100, max_pages=None, **filters):
        for row in sorted(self.rows, key=lambda r: r["initiated_at"]):
            yield row


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


async def test_status_change_becomes_updated_event(tmp_path):
    rows = load_fixture_transactions(FIXTURES)
    client, bus, seen = FakeRhoClient(rows), EventBus(), []
    bus.subscribe(TOPIC_TRANSACTIONS_NEW, collector(seen))
    poller = make_poller(tmp_path, client, bus)
    await poller.tick()
    seen.clear()

    pending = next(r for r in client.rows if r["status"] == "pending")
    pending["status"] = "settled"

    assert await poller.tick() == 1
    assert seen[0].kind == "updated"
    assert seen[0].previous_status == "pending"
    assert seen[0].transaction.id == pending["id"]


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
