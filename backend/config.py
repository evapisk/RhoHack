import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    rho_api_key: str = os.getenv("RHO_API_KEY", "")
    rho_base_url: str = os.getenv("RHO_BASE_URL", "https://sandbox.rho.co/api")
    poll_interval_seconds: float = float(os.getenv("POLL_INTERVAL_SECONDS", "5"))
    anomaly_z_threshold: float = float(os.getenv("ANOMALY_Z_THRESHOLD", "3.0"))
    use_mock_rho: bool = os.getenv("USE_MOCK_RHO", "true").lower() == "true"


settings = Settings()
