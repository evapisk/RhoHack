"""Anomaly scorers. Pick one with SCORER=zscore|autoencoder."""

from __future__ import annotations

import logging

from app.config import Settings
from app.scoring.autoencoder import AutoencoderScorer
from app.scoring.base import Scorer
from app.scoring.zscore import ZScoreScorer

log = logging.getLogger(__name__)


def build_scorer(settings: Settings) -> Scorer:
    zscore = ZScoreScorer(
        min_history=settings.min_history,
        warn_threshold=settings.warn_threshold,
        alert_threshold=settings.alert_threshold,
    )
    if settings.scorer == "autoencoder":
        log.warning("SCORER=autoencoder is a stub; it delegates to zscore until fit()/score() are implemented")
        return AutoencoderScorer(fallback=zscore)
    return zscore


__all__ = ["Scorer", "ZScoreScorer", "AutoencoderScorer", "build_scorer"]
