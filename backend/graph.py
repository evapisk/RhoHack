from __future__ import annotations

import random
import uuid
from datetime import datetime
from typing import Any

import networkx as nx

from .models import Transaction


class TransactionGraph:
    """Wraps a networkx graph of accounts/merchants connected by transactions.

    Node = an entity, either one of our own accounts ("account:acct_001") or
    a counterparty ("merchant:Uber"). Prefixed ids so accounts and merchants
    can never collide even if names overlap.

    Edge = one transaction, account -> counterparty, keyed by transaction id
    (MultiDiGraph, so repeat transactions between the same pair don't
    overwrite each other) and carrying the transaction data as attributes.

    This is Eva's hour 1-4 piece (structure + dummy data) and hour 4-7 piece
    (wiring in real transactions as they arrive from the poller).
    """

    def __init__(self):
        self.graph = nx.MultiDiGraph()

    @staticmethod
    def _account_node(account_id: str) -> str:
        return f"account:{account_id}"

    @staticmethod
    def _merchant_node(name: str) -> str:
        return f"merchant:{name}"

    def add_transaction(self, tx: Transaction) -> None:
        src = self._account_node(tx.account_id)
        dst = self._merchant_node(tx.counterparty)

        self.graph.add_node(src, type="account", account_id=tx.account_id)
        self.graph.add_node(dst, type="merchant", name=tx.counterparty)

        self.graph.add_edge(
            src,
            dst,
            key=tx.id,
            transaction_id=tx.id,
            amount=tx.amount,
            currency=tx.currency,
            timestamp=tx.timestamp,
            category=tx.category,
        )

    def account_history(self, account_id: str) -> list[dict[str, Any]]:
        """All outgoing transaction edges for an account, oldest first. Used
        by scoring.py to build a per-account amount distribution."""
        node = self._account_node(account_id)
        if node not in self.graph:
            return []
        edges = [data for _, _, data in self.graph.out_edges(node, data=True)]
        return sorted(edges, key=lambda e: e["timestamp"])

    def neighbor_merchants(self, account_id: str) -> set[str]:
        node = self._account_node(account_id)
        if node not in self.graph:
            return set()
        return {v for _, v in self.graph.out_edges(node)}

    def load_dummy_data(self, n: int = 20) -> None:
        """TODO(Eva): seed data for early development / demoing the graph
        before the poller is wired in. Fine to delete once real transactions
        are flowing (hour 4-7) — or keep behind a flag for local dev."""
        merchants = ["Uber", "AWS", "Staples", "Delta", "Figma"]
        accounts = ["acct_001", "acct_002"]
        for _ in range(n):
            self.add_transaction(
                Transaction(
                    id=str(uuid.uuid4()),
                    timestamp=datetime.utcnow(),
                    amount=round(random.uniform(5, 500), 2),
                    currency="USD",
                    account_id=random.choice(accounts),
                    counterparty=random.choice(merchants),
                )
            )

    def summary(self) -> dict[str, int]:
        return {"nodes": self.graph.number_of_nodes(), "edges": self.graph.number_of_edges()}
