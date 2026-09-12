from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class Transaction:
    """One transaction pulled from Rho. This is the shape everything downstream
    (graph, scoring, feed) is built around — keep it stable once Eva and Max
    are both wiring against it. If the real Rho payload needs more fields,
    add them here rather than passing raw dicts around.
    """

    id: str
    timestamp: datetime
    amount: float
    currency: str
    account_id: str
    counterparty: str  # merchant / payee name — becomes the "other" graph node
    card_id: Optional[str] = None
    category: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_rho_payload(cls, payload: dict[str, Any]) -> "Transaction":
        """TODO(Max): map the real Rho sandbox transaction JSON -> Transaction
        once we've hit the sandbox and seen the actual schema. This mapping is
        a guess to unblock everyone else in the meantime."""
        return cls(
            id=str(payload["id"]),
            timestamp=datetime.fromisoformat(payload["created_at"]),
            amount=float(payload["amount"]),
            currency=payload.get("currency", "USD"),
            account_id=str(payload["account_id"]),
            counterparty=payload.get("merchant_name") or payload.get("counterparty", "unknown"),
            card_id=payload.get("card_id"),
            category=payload.get("category"),
            raw=payload,
        )


@dataclass
class AnomalyScore:
    transaction_id: str
    score: float
    is_anomalous: bool
    reason: str = ""


@dataclass
class ScoredTransaction:
    """What actually goes out over the feed: a transaction + its score,
    bundled so the frontend never has to join two separate streams."""

    transaction: Transaction
    score: AnomalyScore

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction": {
                "id": self.transaction.id,
                "timestamp": self.transaction.timestamp.isoformat(),
                "amount": self.transaction.amount,
                "currency": self.transaction.currency,
                "account_id": self.transaction.account_id,
                "counterparty": self.transaction.counterparty,
                "card_id": self.transaction.card_id,
                "category": self.transaction.category,
            },
            "score": {
                "value": self.score.score,
                "is_anomalous": self.score.is_anomalous,
                "reason": self.score.reason,
            },
        }
