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

    # 37 distinct counterparty names appear in the feed, but 6 of them are the display
    # names of our own accounts. Those resolve to account nodes, leaving 31 real vendors.
    distinct_names = {(r.get("counterparty_name") or "").strip().lower() for r in rows}
    assert len(distinct_names) == 37
    assert stats["counterparties"] == 31
    assert stats["internal_resolved"] == 28

    assert stats["accounts"] >= len({r["account_id"] for r in rows})
    assert stats["edges"] > 0
    assert stats["population_std_log_amount"] > 0


def test_internal_transfers_resolve_to_account_nodes(fixture_graph):
    """The 6 account display names must not exist as vendor nodes any more."""
    graph, _ = fixture_graph
    vendor_names = {
        d["display_name"].strip().lower()
        for _, d in graph.g.nodes(data=True)
        if d.get("kind") == "counterparty"
    }
    for account_name in ("cash (checking)", "credit account", "rewards", "treasury checking"):
        assert account_name not in vendor_names

    # Ambiguous names collapse into one merged node rather than picking a member.
    merged = [d for n, d in graph.g.nodes(data=True) if n.startswith("account:group:")]
    assert merged, "expected merged nodes for account names covering several accounts"
    assert all(d["kind"] == "account" and len(d["member_ids"]) > 1 for d in merged)


def test_account_to_account_edges_exist(fixture_graph):
    """Before resolution every path between two accounts died in a phantom vendor node."""
    graph, _ = fixture_graph
    internal_edges = [
        (u, v)
        for u, v, d in graph.g.edges(data=True)
        if graph.g.nodes[u].get("kind") == "account" and graph.g.nodes[v].get("kind") == "account"
    ]
    assert internal_edges, "expected real account-to-account edges after resolution"


def test_reset_restores_an_empty_graph():
    """Identity must survive reset: main.py closures and bus subscriptions hold this object.

    Builds its own graph rather than using the module-scoped fixture, which later tests share.
    """
    accounts = load_fixture_accounts(FIXTURES)
    graph = TransactionGraph()
    graph.seed_accounts(accounts)
    for row in load_fixture_transactions(FIXTURES):
        graph.apply(Transaction.from_rho(row))
    before = id(graph.g)

    graph.reset(accounts)

    assert id(graph.g) != before  # the DiGraph itself is replaced
    stats = graph.stats()
    assert stats["transactions"] == 0
    assert stats["counterparties"] == 0
    assert stats["accounts"] == len(accounts)
    assert len(graph.lookalike) == 0

    # and it is usable again afterwards
    f = graph.apply(make_tx("post-reset", -5000, "Coffee Co", "2026-06-01T09:00:00Z"))
    assert f.is_new_counterparty and f.population_tx_count == 0


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


def test_node_link_ids_match_edge_endpoints(fixture_graph):
    """Regression: node attrs carry their own `id`, which used to overwrite the node id
    and leave every edge pointing at an endpoint absent from the payload."""
    graph, _ = fixture_graph
    payload = graph.to_node_link()
    ids = {n["id"] for n in payload["nodes"]}

    assert any(i.startswith("account:") for i in ids)
    dangling = [
        (e["source"], e["target"])
        for e in payload["edges"]
        if e["source"] not in ids or e["target"] not in ids
    ]
    assert dangling == [], f"{len(dangling)} edges reference missing nodes, e.g. {dangling[:1]}"


def test_node_link_omits_bulky_samples_by_default(fixture_graph):
    graph, _ = fixture_graph
    lean = graph.to_node_link()
    assert all("log_amounts" not in e and "tx_ids" not in e for e in lean["edges"])
    assert any("log_amounts" in e for e in graph.to_node_link(include_samples=True)["edges"])
