"""Autoencoder scorer STUB. Same protocol as ZScoreScorer so it can be swapped in via SCORER=autoencoder.

Plan for the real thing (only if hours 4-7 have room):
  1. featurize() below is the input vector; collect one row per backfilled transaction.
  2. fit(): standardize columns, train a small dense autoencoder (e.g. 12 -> 6 -> 3 -> 6 -> 12)
     with torch or sklearn's MLPRegressor on the backfill rows; store per-column mean/std and the
     reconstruction-error distribution (mean, std) on the training set.
  3. score(): reconstruction error -> z against the training error distribution -> same
     1 - exp(-z/3) squash and thresholds as zscore so the UI needs no changes.
Until fit() is implemented, score() delegates to the fallback scorer and tags the model name
so it is obvious in the UI that the autoencoder is not doing the work yet.
"""

from __future__ import annotations

import math
from typing import Sequence

from app.models import AnomalyScore, GraphFeatures, Transaction
from app.scoring.base import Scorer

FEATURE_NAMES: tuple[str, ...] = (
    "log_amount",
    "is_debit",
    "is_card",
    "is_new_counterparty",
    "counterparty_tx_count_log",
    "counterparty_mean_log_amount",
    "counterparty_std_log_amount",
    "account_tx_count_log",
    "account_mean_log_amount",
    "account_std_log_amount",
    "hours_since_last_tx_to_counterparty_log",
    "account_tx_last_hour",
    "population_mean_log_amount",
    "population_std_log_amount",
)


def featurize(tx: Transaction, f: GraphFeatures) -> list[float]:
    """Dense, NaN-free vector in FEATURE_NAMES order."""
    return [
        tx.log_amount,
        1.0 if tx.direction == "debit" else 0.0,
        1.0 if tx.card_id else 0.0,
        1.0 if f.is_new_counterparty else 0.0,
        math.log1p(f.counterparty_tx_count),
        f.counterparty_mean_log_amount or 0.0,
        f.counterparty_std_log_amount or 0.0,
        math.log1p(f.account_tx_count),
        f.account_mean_log_amount or 0.0,
        f.account_std_log_amount or 0.0,
        math.log1p(f.hours_since_last_tx_to_counterparty) if f.hours_since_last_tx_to_counterparty is not None else 0.0,
        float(f.account_tx_last_hour),
        f.population_mean_log_amount or 0.0,
        f.population_std_log_amount or 0.0,
    ]


class AutoencoderScorer:
    name = "autoencoder"

    def __init__(self, fallback: Scorer | None = None) -> None:
        self.fallback = fallback
        self.fitted = False
        self._training_rows: list[list[float]] = []

    def observe(self, tx: Transaction, f: GraphFeatures) -> None:
        """Collect training rows (call during backfill)."""
        self._training_rows.append(featurize(tx, f))

    def fit(self, rows: Sequence[Sequence[float]] | None = None) -> None:
        rows = rows if rows is not None else self._training_rows
        # TODO: standardize columns, train the autoencoder, record reconstruction-error mean/std.
        raise NotImplementedError("AutoencoderScorer.fit is not implemented yet (see module docstring)")

    def score(self, tx: Transaction, f: GraphFeatures) -> AnomalyScore:
        if self.fitted:
            # TODO: reconstruction error -> z -> 1 - exp(-z/3) -> level_for(...)
            raise NotImplementedError("AutoencoderScorer.score is not implemented yet")
        if self.fallback is None:
            raise NotImplementedError("AutoencoderScorer is unfitted and has no fallback scorer")
        result = self.fallback.score(tx, f)
        return result.model_copy(update={"model": f"{self.name}(unfitted->{result.model})"})
