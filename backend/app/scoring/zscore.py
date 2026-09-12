"""Z-score anomaly scorer (the fallback model; swap for the autoencoder later).

Signals, each expressed as a z-like magnitude:
  counterparty_amount_z  |log$ - vendor mean| / vendor std            (needs >= min_history points)
  account_amount_z       |log$ - account mean| / account std          (needs >= min_history points)
  population_amount_z    |log$ - mean of ALL tx seen| / population std   (fallback that keeps a
                         statistical signal alive when the vendor/account have little history)
  new_counterparty       fixed pseudo-z when the vendor has never been seen
  velocity               grows with the number of transactions on the account in the last hour

Combination:
  The three amount z's measure the same thing at different granularity, so only the largest
  counts ("max wins" inside the amount family). Amount, new-vendor and velocity are independent
  evidence and add up (noisy-OR with an exponential link):

      score = 1 - exp(-(amount_z + new_counterparty + velocity) / 3)

  z = 2.1 -> 0.50 (warn), z = 4.2 -> 0.75 (alert). A new vendor alone is a warn; a new vendor
  receiving an unusually large amount is an alert.

Small-sample guard: the std used in a z-score is floored at max(0.25, 1/sqrt(n-1)) in log space,
so two data points cannot make a 3x change look like 5 sigma (n=2 -> 1.0, n=5 -> 0.5, n>=17 -> 0.25).
"""

from __future__ import annotations

import math

from app.models import AnomalyScore, GraphFeatures, Transaction
from app.scoring.base import level_for

STD_FLOOR = 0.25  # log space, for well-observed entities
AMOUNT_COMPONENTS = ("counterparty_amount_z", "account_amount_z", "population_amount_z")


def std_floor(n: int) -> float:
    """Minimum std we are willing to believe after n observations."""
    return max(STD_FLOOR, 1.0 / math.sqrt(max(n - 1, 1)))


class ZScoreScorer:
    name = "zscore"

    def __init__(
        self,
        *,
        min_history: int = 2,
        warn_threshold: float = 0.5,
        alert_threshold: float = 0.75,
        new_counterparty_weight: float = 2.2,
        velocity_threshold: int = 5,
        scale: float = 3.0,
    ) -> None:
        self.min_history = min_history
        self.warn_threshold = warn_threshold
        self.alert_threshold = alert_threshold
        self.new_counterparty_weight = new_counterparty_weight
        self.velocity_threshold = velocity_threshold
        self.scale = scale

    def score(self, tx: Transaction, f: GraphFeatures) -> AnomalyScore:
        x = tx.log_amount
        components: dict[str, float] = {}
        reasons: list[str] = []
        dollars = f"${tx.abs_amount_minor / 100:,.2f}"

        z_cp = self._z(x, f.counterparty_mean_log_amount, f.counterparty_std_log_amount, f.counterparty_tx_count)
        if z_cp is not None:
            components["counterparty_amount_z"] = z_cp
            if z_cp >= 1.0:
                reasons.append(
                    f"{dollars} is {z_cp:.1f}σ {self._dir(x, f.counterparty_mean_log_amount)} the usual amount "
                    f"for '{tx.counterparty_name}' (n={f.counterparty_tx_count})"
                )

        z_acct = self._z(x, f.account_mean_log_amount, f.account_std_log_amount, f.account_tx_count)
        if z_acct is not None:
            components["account_amount_z"] = z_acct
            if z_acct >= 1.0:
                reasons.append(
                    f"{dollars} is {z_acct:.1f}σ {self._dir(x, f.account_mean_log_amount)} the usual amount "
                    f"on account '{tx.account_name or tx.account_id}' (n={f.account_tx_count})"
                )

        z_pop = self._z(x, f.population_mean_log_amount, f.population_std_log_amount, f.population_tx_count)
        if z_pop is not None:
            components["population_amount_z"] = z_pop
            if z_pop >= 1.0:
                reasons.append(
                    f"{dollars} is {z_pop:.1f}σ {self._dir(x, f.population_mean_log_amount)} the typical amount "
                    f"across all {f.population_tx_count} transactions seen so far"
                )

        # Amount signals are correlated: only the strongest one counts.
        evidence = max((components.get(k, 0.0) for k in AMOUNT_COMPONENTS), default=0.0)

        if f.is_new_counterparty:
            components["new_counterparty"] = self.new_counterparty_weight
            reasons.append(f"First ever transaction with '{tx.counterparty_name or 'unknown counterparty'}'")
            evidence += self.new_counterparty_weight

        if f.account_tx_last_hour >= self.velocity_threshold:
            velocity = 2.0 + 0.5 * (f.account_tx_last_hour - self.velocity_threshold)
            components["velocity"] = velocity
            reasons.append(
                f"{f.account_tx_last_hour} transactions on '{tx.account_name or tx.account_id}' in the last hour"
            )
            evidence += velocity

        components["combined_evidence"] = evidence
        score = min(1.0, max(0.0, 1.0 - math.exp(-evidence / self.scale)))
        level = level_for(score, self.warn_threshold, self.alert_threshold)
        return AnomalyScore(
            score=round(score, 4),
            level=level,
            reasons=reasons,
            components={k: round(v, 3) for k, v in components.items()},
            model=self.name,
        )

    def _z(self, x: float, mean: float | None, std: float | None, n: int) -> float | None:
        if mean is None or n < self.min_history:
            return None
        sd = max(std if std is not None else 0.0, std_floor(n))
        return abs(x - mean) / sd

    @staticmethod
    def _dir(x: float, mean: float | None) -> str:
        return "above" if mean is None or x >= mean else "below"
