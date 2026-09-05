from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    database_path: Path = Path(os.environ.get("CEDARHQ_DATABASE_PATH", BASE_DIR / "data" / "cedarhq.sqlite3"))
    secret_key: str = os.environ.get("CEDARHQ_SECRET_KEY", "dev-secret-change-before-production")
    secure_cookies: bool = os.environ.get("CEDARHQ_SECURE_COOKIES", "0") == "1"
    demo_mode: bool = os.environ.get("CEDARHQ_DEMO_MODE", "1") == "1"
    base_url: str = os.environ.get("CEDARHQ_BASE_URL", "http://127.0.0.1:8088")
    session_days: int = int(os.environ.get("CEDARHQ_SESSION_DAYS", "7"))


config = Config()
