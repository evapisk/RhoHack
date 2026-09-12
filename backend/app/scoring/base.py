"""Scorer protocol. Any scorer maps (Transaction, GraphFeatures) -> AnomalyScore."""

from __future__ import annotations

from typing import Protocol

from app.models import AnomalyScore, GraphFeatures, Level, Transaction


class Scorer(Protocol):
    name: str

    def score(self, tx: Transaction, features: GraphFeatures) -> AnomalyScore: ...


def level_for(score: float, warn_threshold: float, alert_threshold: float) -> Level:
    if score >= alert_threshold:
        return "alert"
    if score >= warn_threshold:
        return "warn"
    return "normal"
