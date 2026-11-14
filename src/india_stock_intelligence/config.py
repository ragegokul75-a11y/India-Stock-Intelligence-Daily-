from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = PROJECT_ROOT / "data"
    database_dir: Path | None = None
    stale_after_days: int = 5
    suspicious_change_pct: float = 20.0
    corporate_action_change_pct: float = 30.0
    max_zip_member_bytes: int = 100 * 1024 * 1024
    max_zip_members: int = 100
    nse_sync_enabled: bool = False
    nse_sync_daily_time: str = "18:15"
    nse_sync_poll_interval_minutes: int = 30
    nse_sync_window_minutes: int = 240
    nse_sync_timeout_seconds: int = 20

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "market_data" / "raw"

    @property
    def financial_raw_dir(self) -> Path:
        return self.data_dir / "financial_data" / "raw"

    @property
    def security_master_raw_dir(self) -> Path:
        return self.data_dir / "security_master" / "raw"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def sync_dir(self) -> Path:
        return self.data_dir / "market_data" / "sync" / "nse"

    @property
    def database_path(self) -> Path:
        return (self.database_dir or self.data_dir.parent / "database") / "stock_intelligence.db"


def get_settings() -> Settings:
    """Read local paths and thresholds; providers remain disabled by design."""
    data_dir = Path(os.environ.get("INDIA_STOCK_DATA_DIR", PROJECT_ROOT / "data"))
    default_database_dir = PROJECT_ROOT / "database" if data_dir.resolve() == (PROJECT_ROOT / "data").resolve() else data_dir / "database"
    database_dir = Path(os.environ.get("INDIA_STOCK_DATABASE_DIR", default_database_dir))
    return Settings(
        data_dir=data_dir,
        database_dir=database_dir,
        stale_after_days=int(os.environ.get("INDIA_STOCK_STALE_DAYS", "5")),
        suspicious_change_pct=float(os.environ.get("INDIA_STOCK_SUSPICIOUS_CHANGE_PCT", "20")),
        corporate_action_change_pct=float(os.environ.get("INDIA_STOCK_CORPORATE_ACTION_PCT", "30")),
        nse_sync_enabled=os.environ.get("INDIA_STOCK_NSE_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        nse_sync_daily_time=os.environ.get("INDIA_STOCK_NSE_SYNC_DAILY_TIME", "18:15"),
        nse_sync_poll_interval_minutes=int(os.environ.get("INDIA_STOCK_NSE_SYNC_POLL_MINUTES", "30")),
        nse_sync_window_minutes=int(os.environ.get("INDIA_STOCK_NSE_SYNC_WINDOW_MINUTES", "240")),
        nse_sync_timeout_seconds=int(os.environ.get("INDIA_STOCK_NSE_SYNC_TIMEOUT_SECONDS", "20")),
    )
