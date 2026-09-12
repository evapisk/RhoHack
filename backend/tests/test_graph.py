from pathlib import Path

import pytest

from app.graph.graph import TransactionGraph
from app.models import Money, Transaction
from app.poller.simulator import load_fixture_accounts, load_fixture_transactions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def make_tx(tx_id: str, amount: int, counterparty: str, when: str, account_id: str = "acct-1") -> Transaction:
    return Transaction(
        id=tx_id,
        account_id=account_id,
        account_name="Ops Checking",
        transaction_type="card_debit" if amount < 0 else "ach_credit",
        amount=Money(amount=amount),
        status="settled",
        initiated_at=when,
        counterparty_name=counterparty,
    )


@pytest.fixture(scope="module")
def fixture_graph() -> tuple[TransactionGraph, list[dict]]:
    rows = load_fixture_transactions(FIXTURES)
    graph = TransactionGraph()
    graph.seed_accounts(load_fixture_accounts(FIXTURES))
    for row in rows:
        graph.apply(Transaction.from_rho(row))
    return graph, rows


def test_fixture_builds_expected_graph(fixture_graph):
    graph, rows = fixture_graph
    stats = graph.stats()
    assert stats["transactions"] == len(rows) == 72
    assert stats["counterparties"] == len({(r.get("counterparty_name") or "").strip().lower() for r in rows}) == 37
    assert stats["accounts"] >= len({r["account_id"] for r in rows})
    assert stats["edges"] > 0
    assert stats["population_std_log_amount"] > 0


def test_node_link_is_json_friendly(fixture_graph):
    import json

    graph, _ = fixture_graph
    payload = graph.to_node_link()
    json.dumps(payload)  # datetimes / sets would blow up here
    kinds = {n["kind"] for n in payload["nodes"]}
    assert kinds == {"account", "counterparty"}
    assert all({"source", "target", "tx_count"} <= set(e) for e in payload["edges"])


def test_new_counterparty_flag_flips_after_first_apply():
    graph = TransactionGraph()
    first = graph.apply(make_tx("t1", -5000, "Coffee Co", "2026-06-01T09:00:00Z"))
    second = graph.apply(make_tx("t2", -5200, "Coffee Co", "2026-06-02T09:00:00Z"))

    assert first.is_new_counterparty is True
    assert first.counterparty_tx_count == 0
    assert second.is_new_counterparty is False
    assert second.counterparty_tx_count == 1
    assert second.hours_since_last_tx_to_counterparty == pytest.approx(24.0)


def test_features_are_computed_before_insertion():
    graph = TransactionGraph()
    f = graph.apply(make_tx("t1", -5000, "Coffee Co", "2026-06-01T09:00:00Z"))
    assert f.population_tx_count == 0
    assert f.account_tx_count == 0
    assert graph.stats()["transactions"] == 1


def test_edge_direction_follows_money():
    graph = TransactionGraph()
    graph.apply(make_tx("d", -5000, "Vendor", "2026-06-01T09:00:00Z"))
    graph.apply(make_tx("c", 7000, "Customer", "2026-06-01T10:00:00Z"))
    assert graph.g.has_edge("account:acct-1", "counterparty:vendor")
    assert graph.g.has_edge("counterparty:customer", "account:acct-1")
    assert graph.features_for(make_tx("x", -1, "Whatever", "2026-06-01T11:00:00Z")).account_out_degree == 1


def test_velocity_counts_transactions_in_prior_hour():
    graph = TransactionGraph()
    for i in range(4):
        graph.apply(make_tx(f"t{i}", -1000, f"Shop {i}", f"2026-06-01T09:{i * 10:02d}:00Z"))
    f = graph.features_for(make_tx("probe", -1000, "Shop X", "2026-06-01T09:45:00Z"))
    assert f.account_tx_last_hour == 4
    later = graph.features_for(make_tx("probe2", -1000, "Shop X", "2026-06-01T11:00:00Z"))
    assert later.account_tx_last_hour == 0
