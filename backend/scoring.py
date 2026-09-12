from __future__ import annotations

import statistics
from abc import ABC, abstractmethod

from .config import settings
from .graph import TransactionGraph
from .models import AnomalyScore, Transaction


class AnomalyScorer(ABC):
    """Interface every scorer implements, so main.py can swap ZScoreScorer
    for AutoencoderScorer later without touching the rest of the pipeline."""

    @abstractmethod
    def score(self, tx: Transaction, graph: TransactionGraph) -> AnomalyScore:
        ...


class ZScoreScorer(AnomalyScorer):
    """Fallback / starting point: flag a transaction if its amount is more
    than `threshold` standard deviations from that account's historical
    mean. Cheap, explainable, no training needed — good enough for a live
    demo, and the thing to fall back to if the autoencoder isn't ready.

    This is Max's hour 4-7 piece.
    """

    def __init__(self, threshold: float = settings.anomaly_z_threshold, min_history: int = 5):
        self.threshold = threshold
        self.min_history = min_history

    def score(self, tx: Transaction, graph: TransactionGraph) -> AnomalyScore:
        history = [e["amount"] for e in graph.account_history(tx.account_id) if e["transaction_id"] != tx.id]

        if len(history) < self.min_history:
            return AnomalyScore(tx.id, score=0.0, is_anomalous=False, reason="insufficient history")

        mean = statistics.mean(history)
        stdev = statistics.pstdev(history) or 1e-6
        z = (tx.amount - mean) / stdev
        is_anomalous = abs(z) >= self.threshold
        reason = (
            f"amount {tx.amount:.2f} is {z:.1f} std devs from account mean {mean:.2f}" if is_anomalous else ""
        )
        return AnomalyScore(tx.id, score=z, is_anomalous=is_anomalous, reason=reason)


class AutoencoderScorer(AnomalyScorer):
    """TODO(Max, upgrade path if hour 4-7 has time left): train a small
    autoencoder on per-transaction features (amount, time-of-day, merchant
    embedding, graph degree/centrality) and flag high reconstruction error.
    Stubbed now so main.py can swap it in later with a one-line change."""

    def __init__(self, model=None):
        self.model = model

    def score(self, tx: Transaction, graph: TransactionGraph) -> AnomalyScore:
        raise NotImplementedError("swap in once the autoencoder is trained")
