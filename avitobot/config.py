"""Настройки. Всё через переменные окружения, чтобы код не менялся при переезде на VPS."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv необязателен, переменные могут прийти из окружения
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    admin_id: int = field(default_factory=lambda: _int("ADMIN_ID", 0))

    location_ids: List[int] = field(default_factory=lambda: [
        int(x) for x in (os.getenv("AVITO_LOCATION_IDS", "637640,637780")).split(",") if x.strip()
    ])
    category_id: int = field(default_factory=lambda: _int("AVITO_CATEGORY_ID", 84))
    api_key: str = field(default_factory=lambda: os.getenv(
        "AVITO_API_KEY", "af0deccbgcgidddjgnvljitntccdduijhdinfgjgfjir"))
    proxy: Optional[str] = field(default_factory=lambda: os.getenv("PROXY") or None)

    price_min: int = field(default_factory=lambda: _int("PRICE_MIN", 10_000))
    price_max: int = field(default_factory=lambda: _int("PRICE_MAX", 150_000))
    min_discount: float = field(default_factory=lambda: _float("MIN_DISCOUNT", 0.15))
    scam_floor: float = field(default_factory=lambda: _float("SCAM_FLOOR", 0.45))
    min_sample: int = field(default_factory=lambda: _int("MIN_SAMPLE", 8))
    min_battery: int = field(default_factory=lambda: _int("MIN_BATTERY", 85))
    # На сколько должна упасть цена, чтобы это считалось событием
    min_price_drop: float = field(default_factory=lambda: _float("MIN_PRICE_DROP", 0.05))

    poll_interval: int = field(default_factory=lambda: _int("POLL_INTERVAL", 180))
    pages_per_sweep: int = field(default_factory=lambda: _int("PAGES_PER_SWEEP", 3))
    backfill_pages: int = field(default_factory=lambda: _int("BACKFILL_PAGES", 15))
    requests_per_minute: int = field(default_factory=lambda: _int("REQUESTS_PER_MINUTE", 20))
    median_window_days: int = field(default_factory=lambda: _int("MEDIAN_WINDOW_DAYS", 21))

    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "avito.db"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


settings = Settings()
