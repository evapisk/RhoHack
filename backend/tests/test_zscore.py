from app.graph.graph import TransactionGraph
from app.models import Money, Transaction
from app.scoring.zscore import ZScoreScorer


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


def feed(graph: TransactionGraph, scorer: ZScoreScorer, txs: list[Transaction]):
    results = []
    for tx in txs:
        f = graph.apply(tx)
        results.append(scorer.score(tx, f))
    return results


def routine_history(n: int = 6, vendor: str = "Coffee Co", cents: int = -5000) -> list[Transaction]:
    return [make_tx(f"h{i}", cents + (i % 3) * 150, vendor, f"2026-05-{i + 1:02d}T09:00:00Z") for i in range(n)]


def test_big_outlier_to_known_vendor_is_alert():
    graph, scorer = TransactionGraph(), ZScoreScorer()
    feed(graph, scorer, routine_history())

    outlier = make_tx("big", -5_000_000, "Coffee Co", "2026-05-10T09:00:00Z")  # $50,000 at a $50 vendor
    result = scorer.score(outlier, graph.apply(outlier))

    assert result.level == "alert"
    assert result.components["counterparty_amount_z"] > 4
    assert any("Coffee Co" in r for r in result.reasons)
    assert result.model == "zscore"


def test_routine_repeat_is_normal():
    graph, scorer = TransactionGraph(), ZScoreScorer()
    feed(graph, scorer, routine_history())

    routine = make_tx("again", -5200, "Coffee Co", "2026-05-10T09:00:00Z")
    result = scorer.score(routine, graph.apply(routine))

    assert result.level == "normal"
    assert result.score < 0.5
    assert "new_counterparty" not in result.components


def test_new_vendor_alone_is_a_warning_not_an_alert():
    graph, scorer = TransactionGraph(), ZScoreScorer()
    feed(graph, scorer, routine_history())

    new_vendor = make_tx("nv", -5100, "Brand New Bakery", "2026-05-10T09:00:00Z")
    result = scorer.score(new_vendor, graph.apply(new_vendor))

    assert result.level == "warn"
    assert "new_counterparty" in result.components
    assert any("First ever transaction" in r for r in result.reasons)


def test_population_fallback_flags_huge_amount_to_new_vendor_with_no_entity_history():
    graph, scorer = ZScoreScorer(min_history=2), None  # placeholder to keep names obvious
    graph, scorer = TransactionGraph(), ZScoreScorer(min_history=2)
    # Ten different vendors on ten different accounts: no vendor or account ever reaches min_history.
    history = [make_tx(f"p{i}", -10_000 - i * 500, f"Vendor {i}", f"2026-05-{i + 1:02d}T09:00:00Z", account_id=f"acct-{i}") for i in range(10)]
    feed(graph, scorer, history)

    probe = make_tx("wire", -150_000_000, "Offshore Holdings Ltd", "2026-05-20T09:00:00Z", account_id="acct-new")
    features = graph.apply(probe)
    result = scorer.score(probe, features)

    assert features.counterparty_tx_count == 0 and features.account_tx_count == 0
    assert "counterparty_amount_z" not in result.components
    assert "account_amount_z" not in result.components
    assert result.components["population_amount_z"] > 4
    assert result.level == "alert"
    assert any("across all 10 transactions" in r for r in result.reasons)


def test_min_history_two_lets_second_vendor_transaction_be_scored():
    graph, scorer = TransactionGraph(), ZScoreScorer(min_history=2)
    feed(graph, scorer, [make_tx("a", -5000, "Coffee Co", "2026-05-01T09:00:00Z"), make_tx("b", -5100, "Coffee Co", "2026-05-02T09:00:00Z")])
    third = make_tx("c", -900_000, "Coffee Co", "2026-05-03T09:00:00Z")
    result = scorer.score(third, graph.apply(third))
    assert "counterparty_amount_z" in result.components
    assert result.level == "alert"


def test_velocity_component_triggers_on_burst():
    graph, scorer = TransactionGraph(), ZScoreScorer(velocity_threshold=5)
    burst = [make_tx(f"b{i}", -2000, "Coffee Co", f"2026-05-01T09:{i * 5:02d}:00Z") for i in range(6)]
    results = feed(graph, scorer, burst)
    assert "velocity" in results[-1].components
    assert any("in the last hour" in r for r in results[-1].reasons)


def test_new_vendor_plus_unusual_amount_combine_to_alert():
    """Each signal alone is a warn; together (independent evidence) they are an alert."""
    import math

    graph, scorer = TransactionGraph(), ZScoreScorer()
    feed(graph, scorer, routine_history(n=8))  # ~$50 each on acct-1

    probe = make_tx("nv-big", -12_700, "Brand New Bakery", "2026-05-10T09:00:00Z")  # $127 at a $50 account
    result = scorer.score(probe, graph.apply(probe))

    acct_z = result.components["account_amount_z"]
    assert 2.0 < acct_z < 4.2  # unusual, but not an alert by itself
    assert 1 - math.exp(-acct_z / 3) < 0.75
    assert result.components["new_counterparty"] == 2.2  # a warn by itself
    assert result.level == "alert"


def test_two_observations_do_not_make_a_3x_change_an_alert():
    graph, scorer = TransactionGraph(), ZScoreScorer(min_history=2)
    feed(graph, scorer, [make_tx("a", -5000, "Office Co", "2026-05-01T09:00:00Z"), make_tx("b", -5200, "Office Co", "2026-05-02T09:00:00Z")])
    third = make_tx("c", -18_450, "Office Co", "2026-05-03T09:00:00Z")  # $184.50 after two ~$50 charges
    result = scorer.score(third, graph.apply(third))
    assert result.components["counterparty_amount_z"] < 2.1
    assert result.level == "normal"
