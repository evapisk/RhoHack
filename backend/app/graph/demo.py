"""Build the graph from the sandbox fixture and print what it learned.

    cd backend && uv run python -m app.graph.demo [fixtures_dir]

Lets the graph/ML owner work without the live poller or a Rho key.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.graph.graph import TransactionGraph
from app.models import Transaction
from app.poller.simulator import load_fixture_accounts, load_fixture_transactions
from app.scoring.zscore import ZScoreScorer


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    fixtures_dir = Path(argv[0]) if argv else Path("fixtures")

    rows = load_fixture_transactions(fixtures_dir)
    accounts = load_fixture_accounts(fixtures_dir)

    graph = TransactionGraph()
    graph.seed_accounts(accounts)
    scorer = ZScoreScorer()

    scored = []
    new_counterparties = 0
    for row in rows:
        tx = Transaction.from_rho(row)
        features = graph.apply(tx)
        new_counterparties += features.is_new_counterparty
        scored.append((tx, features, scorer.score(tx, features)))

    stats = graph.stats()
    print(f"fixture rows      : {len(rows)}  ({rows[0]['initiated_at']} -> {rows[-1]['initiated_at']})")
    print(f"accounts seeded   : {len(accounts)}")
    print(f"graph             : {stats['accounts']} account nodes, {stats['counterparties']} counterparty nodes, "
          f"{stats['edges']} edges, {stats['transactions']} transactions applied")
    print(f"new counterparties: {new_counterparties} of {len(rows)} transactions were first contact with a vendor")
    print(f"population log$   : mean={stats['population_mean_log_amount']:.2f} std={stats['population_std_log_amount']:.2f}")

    print("\ntop counterparties by tx_count:")
    for cp in graph.top_counterparties(8):
        print(f"  {cp['display_name']:<32} n={cp['tx_count']:<3} net=${cp['total_minor'] / 100:>14,.2f}")

    levels = {"normal": 0, "warn": 0, "alert": 0}
    for _, _, a in scored:
        levels[a.level] += 1
    print(f"\nz-score levels over the backfill (baseline is learning as it goes): {levels}")

    print("\nhighest-scoring fixture transactions:")
    for tx, f, a in sorted(scored, key=lambda t: t[2].score, reverse=True)[:5]:
        print(f"  {a.level:<6} {a.score:.2f}  {tx.initiated_at:%Y-%m-%d}  ${tx.amount_major:>12,.2f}  "
              f"{tx.transaction_type:<22} {tx.counterparty_name}")
        for reason in a.reasons:
            print(f"         - {reason}")

    print("\nsample GraphFeatures for the last applied transaction:")
    last_tx, last_f, _ = scored[-1]
    print(f"  {last_tx.counterparty_name} ${last_tx.amount_major:,.2f}")
    for k, v in last_f.model_dump().items():
        print(f"    {k:<38} {v}")


if __name__ == "__main__":
    main()
