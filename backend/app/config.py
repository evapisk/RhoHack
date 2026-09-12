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

    # Scoring
    scorer: Literal["zscore", "autoencoder"] = "zscore"
    min_history: int = 2
    warn_threshold: float = 0.5
    alert_threshold: float = 0.75

    # Demo helpers
    demo_replay: bool = False
    demo_replay_interval_seconds: float = 3.0

    # HTTP
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def require_api_key(self) -> None:
        if not self.rho_api_key.strip():
            raise RuntimeError(
                "RHO_API_KEY is empty. Copy backend/.env.example to backend/.env and set it "
                "(the sandbox accepts any non-empty token)."
            )


def get_settings() -> Settings:
    return Settings()
