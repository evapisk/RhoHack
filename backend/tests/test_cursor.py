from datetime import datetime, timezone

from app.models import Money, Transaction
from app.poller.cursor import CursorState


def make_tx(tx_id: str = "t1", status: str = "pending", when: str = "2026-06-01T00:00:00Z") -> Transaction:
    return Transaction(
        id=tx_id,
        account_id="acct-1",
        transaction_type="card_debit",
        amount=Money(amount=-1000),
        status=status,
        initiated_at=when,
        counterparty_name="Coffee Co",
    )


def test_new_then_seen_then_updated():
    cursor = CursorState()
    tx = make_tx()

    assert cursor.classify(tx) == ("new", None)
    cursor.record(tx)
    assert cursor.classify(tx) == (None, "pending")

    settled = make_tx(status="settled")
    assert cursor.classify(settled) == ("updated", "pending")
    cursor.record(settled)
    assert cursor.classify(settled) == (None, "settled")
    assert cursor.seen_count == 1


def test_high_water_mark_tracks_newest_initiated_at():
    cursor = CursorState()
    cursor.record(make_tx("a", when="2026-06-02T00:00:00Z"))
    cursor.record(make_tx("b", when="2026-06-01T00:00:00Z"))  # older, must not move the mark back
    assert cursor.high_water_initiated_at == datetime(2026, 6, 2, tzinfo=timezone.utc).isoformat()


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "state" / "cursor.json"
    cursor = CursorState()
    cursor.record(make_tx("a"))
    cursor.record(make_tx("b", status="settled"))
    cursor.backfill_done = True
    cursor.save(path)

    loaded = CursorState.load(path)
    assert loaded.seen == {"a": "pending", "b": "settled"}
    assert loaded.backfill_done is True
    assert loaded.high_water_initiated_at == cursor.high_water_initiated_at
    assert not path.with_suffix(".json.tmp").exists()


def test_load_missing_or_corrupt_file_starts_fresh(tmp_path):
    assert CursorState.load(tmp_path / "nope.json").seen == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert CursorState.load(bad).seen == {}
