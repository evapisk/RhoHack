"""Vendor impersonation detection: the feature that makes the graph load-bearing."""

from pathlib import Path

import pytest

from app.graph.graph import TransactionGraph
from app.graph.lookalike import LookalikeIndex, significant_tokens
from app.models import Money, Transaction
from app.poller.simulator import load_fixture_accounts, load_fixture_transactions
from app.scoring.zscore import ZScoreScorer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# Real fixture vendor -> a plausible impostor of it
IMPOSTORS = [
    ("Crescent Property Group", "Crescent Property Group LLC"),
    ("Crescent Property Group", "Cresent Property Group"),
    ("Northstar Office Supply", "N0rthstar Office Supply"),
    ("Teamline Software, Inc.", "Teamline Softwares Inc"),
    ("Foundry Works Inc.", "Foundry Work Inc."),
]


def make_tx(tx_id: str, amount: int, counterparty: str, when: str, account_id: str = "acct-1") -> Transaction:
    return Transaction(
        id=tx_id,
        account_id=account_id,
        account_name="Ops Checking",
        transaction_type="ach_debit" if amount < 0 else "ach_credit",
        amount=Money(amount=amount),
        status="settled",
        initiated_at=when,
        counterparty_name=counterparty,
    )


@pytest.fixture(scope="module")
def fixture_graph() -> TransactionGraph:
    graph = TransactionGraph()
    graph.seed_accounts(load_fixture_accounts(FIXTURES))
    for row in load_fixture_transactions(FIXTURES):
        graph.apply(Transaction.from_rho(row))
    return graph


@pytest.mark.parametrize("real,impostor", IMPOSTORS)
def test_impostor_names_are_matched_to_the_real_vendor(fixture_graph, real, impostor):
    probe = make_tx("probe", -50_000, impostor, "2026-07-01T09:00:00Z")
    features = fixture_graph.features_for(probe)  # read-only: does not mutate the graph

    assert features.lookalike is not None, f"{impostor!r} should match {real!r}"
    assert features.lookalike.matched_display_name == real
    assert features.lookalike.ratio >= 0.80
    # the evidence the reason string quotes must come off the real vendor's node
    assert features.lookalike.matched_tx_count >= 1
    assert features.lookalike.matched_total_minor > 0


def test_no_false_positives_across_every_real_vendor_pair(fixture_graph):
    """Each genuine vendor, queried as if new, must not match a different genuine vendor.

    Its own key is excluded. Querying with exclude_key=None let every vendor match itself
    at ratio 1.0, which always outranked a real collision, so the test could not fail.
    """
    index = fixture_graph.lookalike
    vendors = [(d["key"], d["display_name"]) for _, d in fixture_graph.g.nodes(data=True) if d.get("kind") == "counterparty"]
    assert len(vendors) == 31  # 31 * 30 / 2 = 465 pairs

    false_positives = []
    for key, name in vendors:
        match = index.best_match(name, exclude_key=key)
        if match is not None:
            false_positives.append((name, match.display_name, round(match.ratio, 3)))
    assert false_positives == []


def test_known_vendor_is_not_checked_for_impersonation(fixture_graph):
    """A lookalike of a vendor you already pay is just that vendor."""
    probe = make_tx("probe", -5_000, "Crescent Property Group", "2026-07-01T09:00:00Z")
    features = fixture_graph.features_for(probe)
    assert features.is_new_counterparty is False
    assert features.lookalike is None


def test_internal_transfer_is_never_an_impostor(fixture_graph):
    probe = make_tx("probe", -5_000, "Treasury Checking", "2026-07-01T09:00:00Z")
    features = fixture_graph.features_for(probe)
    assert features.is_internal_transfer is True
    assert features.lookalike is None


def test_generic_corporate_suffixes_do_not_create_matches():
    """'Acme Services LLC' and 'Zenith Services LLC' share only stopwords."""
    index = LookalikeIndex()
    index.add("acme services llc", "Acme Services LLC")
    assert index.best_match("Zenith Services LLC") is None
    assert significant_tokens("Acme Services LLC") == ["acme"]


def test_impersonation_alone_reaches_alert(fixture_graph):
    """Weight 4.5 must clear alert unaided: the attack amount is designed to look ordinary."""
    scorer = ZScoreScorer()
    probe = make_tx("probe", -339_100, "Crescent Property Group LLC", "2026-07-01T09:00:00Z",
                    account_id="30000000-0000-4000-8000-000000000002")
    features = fixture_graph.features_for(probe)
    result = scorer.score(probe, features)

    assert result.level == "alert"
    assert result.components["lookalike_vendor"] == 4.5
    assert any("impersonation" in r.lower() for r in result.reasons)
    assert any("Crescent Property Group" in r for r in result.reasons)


def test_lookalike_query_only_runs_for_new_counterparties(fixture_graph):
    """Cost guard: the matcher must not run on the common path."""
    calls = []
    original = fixture_graph.lookalike.best_match
    fixture_graph.lookalike.best_match = lambda *a, **k: (calls.append(a), original(*a, **k))[1]
    try:
        fixture_graph.features_for(make_tx("known", -5_000, "Crescent Property Group", "2026-07-01T09:00:00Z"))
        assert calls == []
        fixture_graph.features_for(make_tx("new", -5_000, "Someone Brand New Ltd", "2026-07-01T09:00:00Z"))
        assert len(calls) == 1
    finally:
        fixture_graph.lookalike.best_match = original
