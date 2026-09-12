"""Settings loaded from environment / backend/.env (never hardcode the Rho key)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Rho
    rho_api_key: str = Field(default="", description="Bearer token. Sandbox accepts any non-empty value.")
    rho_base_url: str = "https://rhoapi-sandbox.rho.co/api/v1"

    # Poller
    poll_interval_seconds: float = 5.0
    page_size: int = Field(default=100, ge=1, le=100)
    backfill_on_start: bool = True
    state_dir: Path = Path(".state")
    fixtures_dir: Path = Path("fixtures")
    # Every Nth tick, also re-check every transaction last seen pending/awaiting_approval
    # by id (see Poller.sweep_pending). The incremental tick alone won't catch a
    # settlement on something older than the high-water mark.
    pending_sweep_every_n_ticks: int = Field(default=6, ge=1)

    # Scoring
    scorer: Literal["zscore", "autoencoder"] = "zscore"
    min_history: int = 2
    warn_threshold: float = 0.5
    alert_threshold: float = 0.75

    # Demo helpers
    demo_offline: bool = False  # force fixture mode, to rehearse the no-wifi path
    demo_replay: bool = False
    demo_replay_interval_seconds: float = 3.0

    # HTTP
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def api_key_problem(self) -> str | None:
        """Returns the setup message when the key is missing, rather than raising.

        A missing key used to abort startup, which meant the app could not run at all
        without network or config. It now degrades to fixture mode instead, so the
        message is a warning and the wording is unchanged.
        """
        if not self.rho_api_key.strip():
            return (
                "RHO_API_KEY is empty. Copy backend/.env.example to backend/.env and set it "
                "(the sandbox accepts any non-empty token)."
            )
        return None


def get_settings() -> Settings:
    return Settings()
