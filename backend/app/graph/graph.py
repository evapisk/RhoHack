"""TransactionGraph: a networkx DiGraph plus the running statistics that turn a
transaction into GraphFeatures.

    graph = TransactionGraph()
    graph.seed_accounts(accounts)   # optional, from GET /accounts
    features = graph.apply(tx)      # features computed BEFORE tx is inserted
    features = graph.features_for(tx)   # read-only (used for status updates)

Features are computed before insertion so the first transaction with a vendor reads as
new, and a transaction's own amount never contaminates the baseline it is judged against.

Two things make the structure load-bearing rather than decorative:
  - an AccountResolver turns internal transfers into real account-to-account edges
    instead of phantom vendor nodes, so 2-hop paths between your own accounts exist;
  - a LookalikeIndex over the vendor nodes lets a brand-new counterparty be checked
    against every vendor you already pay.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

import networkx as nx

from app.graph.lookalike import LookalikeIndex
from app.graph.resolver import AccountResolver, ResolvedCounterparty, account_node_id, counterparty_node_id
from app.graph.schema import AccountNode, CounterpartyNode, TransactionEdge
from app.models import GraphFeatures, LookalikeMatch, Transaction

VELOCITY_WINDOW = timedelta(hours=1)
TIME_RETENTION = timedelta(hours=24)

__all__ = ["TransactionGraph", "RunningStats", "account_node_id", "counterparty_node_id"]


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


class TransactionGraph:
    def __init__(self, resolver: AccountResolver | None = None) -> None:
        # Defaulted so TransactionGraph() keeps working for tests and app.graph.demo.
        self.resolver = resolver or AccountResolver()
        self.lookalike = LookalikeIndex()
        self._init_state()

    def _init_state(self) -> None:
        self.g = nx.DiGraph()
        self._account_stats: dict[str, RunningStats] = defaultdict(RunningStats)
        self._counterparty_stats: dict[str, RunningStats] = defaultdict(RunningStats)
        self._population = RunningStats()
        self._account_times: dict[str, deque[datetime]] = defaultdict(deque)
        self._counterparty_last_seen: dict[str, datetime] = {}
        self.transactions_applied = 0
        self.internal_resolved = 0

    def reset(self, accounts: list[dict[str, Any]] | None = None) -> None:
        """Return to an empty graph, preserving object identity.

        The demo reset and every scenario run go through here. Identity matters: routes
        and bus subscriptions in main.py close over this object, so replacing it would
        leave stale handlers registered and score every transaction twice.
        """
        self._init_state()
        self.resolver.clear()
        self.lookalike.clear()
        if accounts:
            self.seed_accounts(accounts)

    # -- seeding -----------------------------------------------------------------

    def seed_accounts(self, accounts: list[dict[str, Any]]) -> int:
        self.resolver.seed(accounts)
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

    def index_money_movements(self, rows: list[dict[str, Any]]) -> int:
        """Pair the two legs of each internal transfer before the rows are scored."""
        return self.resolver.index_money_movements(rows)

    # -- features -----------------------------------------------------------------

    def features_for(self, tx: Transaction) -> GraphFeatures:
        res = self.resolver.resolve(tx)
        cp_stats = self._counterparty_stats.get(res.stats_key)
        acct_stats = self._account_stats.get(tx.account_id)
        acct_nid = account_node_id(tx.account_id)
        is_new = cp_stats is None or cp_stats.n == 0

        last_seen = self._counterparty_last_seen.get(res.stats_key)
        hours_since = None
        if last_seen is not None:
            hours_since = max(0.0, (tx.initiated_at - last_seen).total_seconds() / 3600)

        times = self._account_times.get(tx.account_id, ())
        last_hour = sum(1 for t in times if timedelta(0) <= (tx.initiated_at - t) <= VELOCITY_WINDOW)

        # Only a genuinely new external vendor is worth checking for impersonation:
        # a lookalike of someone you have already paid ten times is just that vendor.
        lookalike: LookalikeMatch | None = None
        if is_new and res.is_external:
            lookalike = self._match_lookalike(tx, res)

        return GraphFeatures(
            is_new_counterparty=is_new,
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
            is_internal_transfer=res.is_internal,
            counterparty_node_kind=res.node_kind,
            counterparty_resolution=res.confidence,
            account_node_id=acct_nid,
            counterparty_node_id=res.node_id,
            counterparty_account_count=self._account_fan_in(res.node_id),
            lookalike=lookalike,
        )

    def _match_lookalike(self, tx: Transaction, res: ResolvedCounterparty) -> LookalikeMatch | None:
        candidate = self.lookalike.best_match(tx.counterparty_name, exclude_key=res.stats_key)
        if candidate is None:
            return None
        nid = counterparty_node_id(candidate.key)
        attrs = self.g.nodes[nid] if nid in self.g else {}
        return LookalikeMatch(
            matched_key=candidate.key,
            matched_node_id=nid,
            matched_display_name=candidate.display_name,
            ratio=round(candidate.ratio, 4),
            shared_tokens=candidate.shared_tokens,
            matched_tx_count=int(attrs.get("tx_count", 0)),
            matched_total_minor=abs(int(attrs.get("total_minor", 0))),
            matched_last_seen=attrs.get("last_seen"),
        )

    def _account_fan_in(self, node_id: str) -> int:
        """2-hop traversal: how many of our accounts transact with this counterparty."""
        if node_id not in self.g:
            return 0
        peers = set(self.g.predecessors(node_id)) | set(self.g.successors(node_id))
        return sum(1 for p in peers if self.g.nodes[p].get("kind") == "account")

    def apply(self, tx: Transaction) -> GraphFeatures:
        features = self.features_for(tx)
        self.add(tx)
        return features

    # -- mutation -----------------------------------------------------------------

    def add(self, tx: Transaction) -> None:
        stamp = tx.initiated_at.isoformat()
        res = self.resolver.resolve(tx)
        acct_nid = account_node_id(tx.account_id)

        if acct_nid not in self.g:
            self.g.add_node(
                acct_nid,
                **AccountNode(
                    id=tx.account_id, name=tx.account_name or tx.account_id, account_type=tx.account_type
                ).to_attrs(),
            )
        self.g.nodes[acct_nid]["tx_count"] += 1

        peer_nid = self._ensure_peer_node(res, tx, stamp)
        peer = self.g.nodes[peer_nid]
        peer["tx_count"] += 1
        peer["total_minor"] = peer.get("total_minor", 0) + tx.amount.amount
        peer["last_seen"] = max(peer.get("last_seen") or stamp, stamp)

        source, target = (acct_nid, peer_nid) if tx.direction == "debit" else (peer_nid, acct_nid)
        if not self.g.has_edge(source, target):
            edge_attrs = TransactionEdge(source=source, target=target, first_seen=stamp).to_attrs()
            edge_attrs["internal"] = res.is_internal
            self.g.add_edge(source, target, **edge_attrs)
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
        self._counterparty_stats[res.stats_key].add(x)
        self._population.add(x)

        prev = self._counterparty_last_seen.get(res.stats_key)
        if prev is None or tx.initiated_at > prev:
            self._counterparty_last_seen[res.stats_key] = tx.initiated_at

        times = self._account_times[tx.account_id]
        times.append(tx.initiated_at)
        newest = max(times)
        while times and newest - times[0] > TIME_RETENTION:
            times.popleft()

        self.transactions_applied += 1
        if res.is_internal:
            self.internal_resolved += 1

    def _ensure_peer_node(self, res: ResolvedCounterparty, tx: Transaction, stamp: str) -> str:
        """Create the counterparty side of the edge, as an account or a vendor node."""
        if res.node_id in self.g:
            return res.node_id

        if res.node_kind == "account":
            # A merged node standing for several same-named sibling accounts, or an
            # account we never saw in GET /accounts. Kept as kind="account" so the
            # node vocabulary stays {account, counterparty}.
            self.g.add_node(
                res.node_id,
                **AccountNode(id=res.node_id, name=res.display_name).to_attrs(),
                **({"ambiguous": True, "member_ids": list(res.member_ids)} if len(res.member_ids) > 1 else {}),
            )
            self.g.nodes[res.node_id].setdefault("total_minor", 0)
            self.g.nodes[res.node_id].setdefault("last_seen", stamp)
            return res.node_id

        self.g.add_node(
            res.node_id,
            **CounterpartyNode(key=res.stats_key, display_name=res.display_name, first_seen=stamp).to_attrs(),
        )
        # Only real vendors enter the impersonation index.
        self.lookalike.add(res.stats_key, res.display_name)
        return res.node_id

    # -- read side ----------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        accounts = sum(1 for _, d in self.g.nodes(data=True) if d.get("kind") == "account")
        counterparties = sum(1 for _, d in self.g.nodes(data=True) if d.get("kind") == "counterparty")
        return {
            "accounts": accounts,
            "counterparties": counterparties,
            "edges": self.g.number_of_edges(),
            "transactions": self.transactions_applied,
            "internal_resolved": self.internal_resolved,
            "population_mean_log_amount": self._population.mean_or_none(),
            "population_std_log_amount": self._population.std,
        }

    def top_counterparties(self, n: int = 10) -> list[dict[str, Any]]:
        cps = [d for _, d in self.g.nodes(data=True) if d.get("kind") == "counterparty"]
        cps.sort(key=lambda d: d["tx_count"], reverse=True)
        return cps[:n]

    def to_node_link(self, *, include_samples: bool = False, min_tx: int = 0) -> dict[str, Any]:
        """JSON-friendly node/edge lists for the frontend graph view.

        `log_amounts` and `tx_ids` can hold 500 entries per edge, which is dead weight
        over the wire, so they are omitted unless explicitly requested.
        """
        drop = set() if include_samples else {"log_amounts", "tx_ids"}
        # The spread goes FIRST: node and edge attrs carry their own id/source/target
        # (AccountNode.id is the bare Rho account id, not the "account:<id>" node id),
        # and letting them win would leave every edge pointing at a node that is not
        # in the payload.
        edges = [
            {**{k: val for k, val in attrs.items() if k not in drop}, "source": u, "target": v}
            for u, v, attrs in self.g.edges(data=True)
            if attrs.get("tx_count", 0) >= min_tx
        ]
        keep = {e["source"] for e in edges} | {e["target"] for e in edges}
        nodes = [
            {**attrs, "id": nid}
            for nid, attrs in self.g.nodes(data=True)
            if min_tx <= 0 or nid in keep
        ]
        return {"nodes": nodes, "edges": edges, "stats": self.stats()}
