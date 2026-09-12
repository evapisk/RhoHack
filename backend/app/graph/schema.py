"""Shapes of what lives in the graph.

Node ids in the networkx graph are strings:
    "account:<rho account id>"          -> attrs = AccountNode fields
    "counterparty:<normalized name>"    -> attrs = CounterpartyNode fields

Edge direction follows the money:
    debit  (amount < 0): account -> counterparty
    credit (amount > 0): counterparty -> account
One edge per (source, target) pair aggregates every transaction between them (TransactionEdge).

Rho gives no counterparty id, so vendors key on a normalized display name. Internal
transfers show the other account's *name* as the counterparty ("Cash (Checking)"), so
they appear as counterparty nodes too. Good enough for anomaly features; a later pass
could resolve those to account nodes by name.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class AccountNode:
    id: str  # Rho account id
    name: str
    account_type: str | None = None  # checking | savings | credit | investment | rewards
    balance_minor: int | None = None
    currency: str = "USD"
    tx_count: int = 0
    kind: Literal["account"] = "account"

    def to_attrs(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CounterpartyNode:
    key: str  # normalized name
    display_name: str
    tx_count: int = 0
    total_minor: int = 0  # signed sum from the company's point of view
    first_seen: str | None = None  # ISO timestamps (JSON friendly)
    last_seen: str | None = None
    kind: Literal["counterparty"] = "counterparty"

    def to_attrs(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TransactionEdge:
    source: str  # node id
    target: str  # node id
    tx_count: int = 0
    total_minor: int = 0  # sum of absolute amounts
    log_amounts: list[float] = field(default_factory=list)  # capped, for viz / debugging
    first_seen: str | None = None
    last_seen: str | None = None
    tx_ids: list[str] = field(default_factory=list)  # capped
    transaction_types: list[str] = field(default_factory=list)

    MAX_TRACKED = 500

    def to_attrs(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("MAX_TRACKED", None)
        return data
