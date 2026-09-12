"""Shared data contract for the whole pipeline.

Every stage (poller -> graph -> scorer -> SSE) talks only in these models.
Both halves of the team build against this file, so change it deliberately.

Flow:
    TransactionEvent  (emitted by the poller / replay / inject)
        -> TransactionGraph.apply(tx) -> GraphFeatures
        -> Scorer.score(tx, features) -> AnomalyScore
        -> ScoredTransaction          (pushed over SSE and served by /api/transactions)
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EventKind = Literal["new", "updated"]
EventSource = Literal["rho", "replay", "inject"]
Level = Literal["normal", "warn", "alert"]
NodeKind = Literal["account", "counterparty"]
# How a transaction's counterparty was resolved to a graph node. "exact" and the two
# name tiers mean it was one of our own accounts (an internal transfer), not a vendor.
Resolution = Literal["exact", "unique_name", "ambiguous_name", "external"]


def normalize_counterparty(name: str | None) -> str:
    """Rho exposes no counterparty id, so vendor nodes key on a normalized display name."""
    cleaned = re.sub(r"\s+", " ", (name or "").strip().lower())
    return cleaned or "unknown"


class Money(BaseModel):
    amount: int  # signed, minor units (cents). Negative = money leaving the account.
    currency: str = "USD"


class Transaction(BaseModel):
    """A Rho transaction, normalized. Field names match Rho's API 1:1 where they exist."""

    id: str
    money_movement_id: str | None = None
    account_id: str
    account_name: str | None = None
    account_type: str | None = None  # checking | savings | credit | investment | rewards
    transaction_type: str  # card_debit, ach_debit, wire_out, credit_repayment, ...
    amount: Money
    status: str  # pending | settled | failed | awaiting_approval
    initiated_at: datetime
    posted_at: datetime | None = None  # null until settled/failed
    user_id: str | None = None
    user_full_name: str | None = None
    card_id: str | None = None
    card_name: str | None = None
    counterparty_name: str | None = None
    memo: str | None = None
    note: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    @classmethod
    def from_rho(cls, row: dict[str, Any]) -> "Transaction":
        amt = row.get("amount") or {}
        return cls(
            id=row["id"],
            money_movement_id=row.get("money_movement_id"),
            account_id=row["account_id"],
            account_name=row.get("account_name"),
            account_type=row.get("account_type"),
            transaction_type=row.get("transaction_type", "unknown"),
            amount=Money(amount=int(amt.get("amount", 0)), currency=amt.get("currency", "USD")),
            status=row.get("status", "unknown"),
            initiated_at=row["initiated_at"],
            posted_at=row.get("posted_at"),
            user_id=row.get("user_id"),
            user_full_name=row.get("user_full_name"),
            card_id=row.get("card_id"),
            card_name=row.get("card_name"),
            counterparty_name=row.get("counterparty_name"),
            memo=row.get("memo"),
            note=row.get("note"),
            raw=row,
        )

    @property
    def abs_amount_minor(self) -> int:
        return abs(self.amount.amount)

    @property
    def amount_major(self) -> float:
        return self.amount.amount / 100

    @property
    def direction(self) -> Literal["debit", "credit"]:
        return "debit" if self.amount.amount < 0 else "credit"

    @property
    def counterparty_key(self) -> str:
        return normalize_counterparty(self.counterparty_name)

    @property
    def log_amount(self) -> float:
        """log1p of the absolute amount in major units. Amounts are heavy-tailed ($7 .. $1.3M)."""
        return math.log1p(self.abs_amount_minor / 100)


class TransactionEvent(BaseModel):
    """What the poller (or replay / inject) emits on the `transactions.new` topic."""

    kind: EventKind = "new"
    transaction: Transaction
    backfill: bool = False  # True while walking history on first start; UI dims these
    source: EventSource = "rho"
    previous_status: str | None = None  # set on kind == "updated"


class LookalikeMatch(BaseModel):
    """An existing counterparty whose name is suspiciously close to a brand-new one.

    Vendor impersonation (business email compromise) is the top B2B payment fraud
    pattern: an invoice arrives from a name one character off a vendor you already
    pay, with new bank details. The graph's vendor set is what makes it recognizable.
    """

    matched_key: str
    matched_node_id: str
    matched_display_name: str
    ratio: float  # difflib.SequenceMatcher ratio, 0..1
    shared_tokens: list[str] = Field(default_factory=list)
    matched_tx_count: int = 0
    matched_total_minor: int = 0  # absolute total already paid to the real vendor
    matched_last_seen: str | None = None


class GraphFeatures(BaseModel):
    """What TransactionGraph returns for a transaction, computed BEFORE inserting it.

    Every field added after the initial contract carries a default, so constructing
    GraphFeatures anywhere other than TransactionGraph.features_for stays valid.
    """

    is_new_counterparty: bool
    counterparty_tx_count: int
    counterparty_mean_log_amount: float | None
    counterparty_std_log_amount: float | None
    account_tx_count: int
    account_mean_log_amount: float | None
    account_std_log_amount: float | None
    hours_since_last_tx_to_counterparty: float | None
    account_out_degree: int  # distinct peers this account has paid (see note below)
    account_tx_last_hour: int  # velocity, relative to the transaction's own timestamp
    population_tx_count: int  # everything seen so far, all accounts / counterparties
    population_mean_log_amount: float | None
    population_std_log_amount: float | None

    # --- graph-derived, added after internal-transfer resolution landed ---
    # NOTE: account_out_degree now counts sibling accounts too, not just vendors.
    is_internal_transfer: bool = False
    counterparty_node_kind: NodeKind = "counterparty"
    counterparty_resolution: Resolution = "external"
    account_node_id: str = ""
    counterparty_node_id: str = ""
    # 2-hop traversal: how many of our accounts transact with this counterparty.
    # Computed and displayed, deliberately NOT scored: on the Rho sandbox every real
    # vendor has fan-in 1, so weighting it would only ever fire on injected data.
    counterparty_account_count: int = 0
    lookalike: LookalikeMatch | None = None


class AnomalyScore(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    level: Level
    reasons: list[str] = Field(default_factory=list)
    components: dict[str, float] = Field(default_factory=dict)  # z-like magnitudes per signal
    model: str


class ScoredTransaction(BaseModel):
    """The unit that goes over SSE and out of /api/transactions."""

    event: TransactionEvent
    features: GraphFeatures
    anomaly: AnomalyScore
    scored_at: datetime
