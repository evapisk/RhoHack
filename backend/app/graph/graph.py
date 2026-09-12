"""TransactionGraph: networkx DiGraph + running statistics that turn a transaction into GraphFeatures.

Usage:
    graph = TransactionGraph()
    graph.seed_accounts(accounts)            # optional, from GET /accounts
    features = graph.apply(tx)               # features computed BEFORE tx is inserted
    features = graph.features_for(tx)        # read-only (used for status updates)

Features are computed before insertion so the first transaction with a vendor is
"new", and a transaction's own amount never contaminates the baseline it's judged against.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

import networkx as nx

from app.graph.schema import AccountNode, CounterpartyNode, TransactionEdge
from app.models import GraphFeatures, Transaction

VELOCITY_WINDOW = timedelta(hours=1)
TIME_RETENTION = timedelta(hours=24)


class RunningStats:
    """Welford online mean / sample std."""

    __slots__ = ("n", "mean", "_m2")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (x - self.mean)

    @property
    def std(self) -> float | None:
        if self.n < 2:
            return None
        return math.sqrt(self._m2 / (self.n - 1))

    def mean_or_none(self) -> float | None:
        return self.mean if self.n else None


def account_node_id(account_id: str) -> str:
    return f"account:{account_id}"


def counterparty_node_id(key: str) -> str:
    return f"counterparty:{key}"


class TransactionGraph:
    def __init__(self) -> None:
        self.g = nx.DiGraph()
        self._account_stats: dict[str, RunningStats] = defaultdict(RunningStats)
        self._counterparty_stats: dict[str, RunningStats] = defaultdict(RunningStats)
        self._population = RunningStats()
        self._account_times: dict[str, deque[datetime]] = defaultdict(deque)
        self._counterparty_last_seen: dict[str, datetime] = {}
        self.transactions_applied = 0

    # -- seeding -----------------------------------------------------------------

    def seed_accounts(self, accounts: list[dict[str, Any]]) -> int:
        for acct in accounts:
            balance = acct.get("balance") or {}
            node = AccountNode(
                id=acct["id"],
                name=acct.get("account_name") or acct["id"],
                account_type=acct.get("account_type"),
                balance_minor=balance.get("amount"),
                currency=balance.get("currency", "USD"),
            )
            nid = account_node_id(node.id)
            if nid in self.g:
                self.g.nodes[nid].update({k: v for k, v in node.to_attrs().items() if k != "tx_count"})
            else:
                self.g.add_node(nid, **node.to_attrs())
        return len(accounts)

    # -- features -----------------------------------------------------------------

    def features_for(self, tx: Transaction) -> GraphFeatures:
        cp_key = tx.counterparty_key
        cp_stats = self._counterparty_stats.get(cp_key)
        acct_stats = self._account_stats.get(tx.account_id)
        acct_nid = account_node_id(tx.account_id)

        last_seen = self._counterparty_last_seen.get(cp_key)
        hours_since = None
        if last_seen is not None:
            hours_since = max(0.0, (tx.initiated_at - last_seen).total_seconds() / 3600)

        times = self._account_times.get(tx.account_id, ())
        last_hour = sum(1 for t in times if timedelta(0) <= (tx.initiated_at - t) <= VELOCITY_WINDOW)

        return GraphFeatures(
            is_new_counterparty=cp_stats is None or cp_stats.n == 0,
            counterparty_tx_count=cp_stats.n if cp_stats else 0,
            counterparty_mean_log_amount=cp_stats.mean_or_none() if cp_stats else None,
            counterparty_std_log_amount=cp_stats.std if cp_stats else None,
            account_tx_count=acct_stats.n if acct_stats else 0,
            account_mean_log_amount=acct_stats.mean_or_none() if acct_stats else None,
            account_std_log_amount=acct_stats.std if acct_stats else None,
            hours_since_last_tx_to_counterparty=hours_since,
            account_out_degree=self.g.out_degree(acct_nid) if acct_nid in self.g else 0,
            account_tx_last_hour=last_hour,
            population_tx_count=self._population.n,
            population_mean_log_amount=self._population.mean_or_none(),
            population_std_log_amount=self._population.std,
        )

    def apply(self, tx: Transaction) -> GraphFeatures:
        features = self.features_for(tx)
        self.add(tx)
        return features

    # -- mutation -----------------------------------------------------------------

    def add(self, tx: Transaction) -> None:
        stamp = tx.initiated_at.isoformat()
        acct_nid = account_node_id(tx.account_id)
        cp_key = tx.counterparty_key
        cp_nid = counterparty_node_id(cp_key)

        if acct_nid not in self.g:
            self.g.add_node(
                acct_nid,
                **AccountNode(id=tx.account_id, name=tx.account_name or tx.account_id, account_type=tx.account_type).to_attrs(),
            )
        self.g.nodes[acct_nid]["tx_count"] += 1

        if cp_nid not in self.g:
            self.g.add_node(
                cp_nid,
                **CounterpartyNode(key=cp_key, display_name=tx.counterparty_name or "Unknown", first_seen=stamp).to_attrs(),
            )
        cp_attrs = self.g.nodes[cp_nid]
        cp_attrs["tx_count"] += 1
        cp_attrs["total_minor"] += tx.amount.amount
        cp_attrs["last_seen"] = max(cp_attrs.get("last_seen") or stamp, stamp)

        source, target = (acct_nid, cp_nid) if tx.direction == "debit" else (cp_nid, acct_nid)
        if not self.g.has_edge(source, target):
            self.g.add_edge(source, target, **TransactionEdge(source=source, target=target, first_seen=stamp).to_attrs())
        edge = self.g.edges[source, target]
        edge["tx_count"] += 1
        edge["total_minor"] += tx.abs_amount_minor
        edge["last_seen"] = max(edge.get("last_seen") or stamp, stamp)
        if len(edge["log_amounts"]) < TransactionEdge.MAX_TRACKED:
            edge["log_amounts"].append(round(tx.log_amount, 4))
            edge["tx_ids"].append(tx.id)
        if tx.transaction_type not in edge["transaction_types"]:
            edge["transaction_types"].append(tx.transaction_type)

        x = tx.log_amount
        self._account_stats[tx.account_id].add(x)
        self._counterparty_stats[cp_key].add(x)
        self._population.add(x)

        prev = self._counterparty_last_seen.get(cp_key)
        if prev is None or tx.initiated_at > prev:
            self._counterparty_last_seen[cp_key] = tx.initiated_at

        times = self._account_times[tx.account_id]
        times.append(tx.initiated_at)
        newest = max(times)
        while times and newest - times[0] > TIME_RETENTION:
            times.popleft()

        self.transactions_applied += 1

    # -- read side ----------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        accounts = sum(1 for _, d in self.g.nodes(data=True) if d.get("kind") == "account")
        counterparties = sum(1 for _, d in self.g.nodes(data=True) if d.get("kind") == "counterparty")
        return {
            "accounts": accounts,
            "counterparties": counterparties,
            "edges": self.g.number_of_edges(),
            "transactions": self.transactions_applied,
            "population_mean_log_amount": self._population.mean_or_none(),
            "population_std_log_amount": self._population.std,
        }

    def top_counterparties(self, n: int = 10) -> list[dict[str, Any]]:
        cps = [d for _, d in self.g.nodes(data=True) if d.get("kind") == "counterparty"]
        cps.sort(key=lambda d: d["tx_count"], reverse=True)
        return cps[:n]

    def to_node_link(self) -> dict[str, Any]:
        """JSON-friendly node/edge lists for the frontend graph view.

        The graph key (`nid`, e.g. "account:<rho id>" / "counterparty:<key>") must win
        as the `id` field: it's what edges' source/target reference. AccountNode also
        has its own `id` attribute holding the raw (unprefixed) Rho account id, so
        `{"id": nid, **attrs}` would let `attrs["id"]` silently clobber `nid` and orphan
        every edge touching an account. Attrs go first, graph-truth fields go last.
        """
        nodes = [{**attrs, "id": nid} for nid, attrs in self.g.nodes(data=True)]
        edges = [{**attrs, "source": u, "target": v} for u, v, attrs in self.g.edges(data=True)]
        return {"nodes": nodes, "edges": edges, "stats": self.stats()}
