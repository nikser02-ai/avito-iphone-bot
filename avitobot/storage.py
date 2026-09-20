"""SQLite: объявления, ценовой индекс, подписчики, дедупликация отправок."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    price         INTEGER NOT NULL,
    category      TEXT,
    model         TEXT,
    variant       INTEGER,
    battery       INTEGER,
    url           TEXT,
    image         TEXT,
    region        TEXT,
    seller_rating REAL,
    seller_reviews INTEGER DEFAULT 0,
    photos        INTEGER DEFAULT 0,
    is_clean      INTEGER DEFAULT 0,
    flags         TEXT DEFAULT '',
    first_seen    INTEGER NOT NULL,
    last_seen     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_index ON listings(model, variant, is_clean, first_seen);

CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    active       INTEGER DEFAULT 1,
    price_min    INTEGER,
    price_max    INTEGER,
    models       TEXT DEFAULT '',
    categories   TEXT DEFAULT 'iphone,watch',
    min_discount REAL,
    min_battery  INTEGER,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sent (
    user_id    INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    sent_at    INTEGER NOT NULL,
    PRIMARY KEY (user_id, listing_id)
);

CREATE TABLE IF NOT EXISTS saved (
    user_id    INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    saved_at   INTEGER NOT NULL,
    PRIMARY KEY (user_id, listing_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass
class User:
    user_id: int
    username: str = ""
    active: bool = True
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    models: str = ""
    categories: str = "iphone,watch"
    min_discount: Optional[float] = None
    min_battery: Optional[int] = None

    @property
    def model_list(self) -> List[str]:
        return [m.strip() for m in self.models.split(",") if m.strip()]

    @property
    def category_list(self) -> List[str]:
        return [c.strip() for c in (self.categories or "").split(",") if c.strip()]


class Storage:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        if not self.get_meta("created_at"):
            self.set_meta("created_at", str(int(time.time())))

    def _migrate(self) -> None:
        """Догоняет схему на базах, созданных до появления Apple Watch."""
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(listings)")}
        if "storage" in columns and "variant" not in columns:
            self.conn.execute("ALTER TABLE listings RENAME COLUMN storage TO variant")
        if "category" not in columns:
            self.conn.execute("ALTER TABLE listings ADD COLUMN category TEXT DEFAULT 'iphone'")
        user_columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}
        if "categories" not in user_columns:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN categories TEXT DEFAULT 'iphone,watch'")

    # -------------------------------------------------------------- объявления

    def upsert_listing(self, **row) -> Tuple[bool, Optional[int]]:
        """Сохраняет объявление.

        Возвращает (впервые ли видим, прежняя цена). Прежняя цена нужна,
        чтобы поймать снижение: продавец уронил ценник — это тоже событие.
        """
        now = int(time.time())
        cur = self.conn.execute("SELECT price FROM listings WHERE id = ?", (row["id"],))
        previous = cur.fetchone()
        is_new = previous is None
        previous_price = None if is_new else previous["price"]
        if is_new:
            self.conn.execute(
                """INSERT INTO listings
                   (id, title, price, category, model, variant, battery, url, image, region,
                    seller_rating, seller_reviews, photos, is_clean, flags,
                    first_seen, last_seen)
                   VALUES (:id, :title, :price, :category, :model, :variant, :battery, :url,
                           :image, :region, :seller_rating, :seller_reviews, :photos,
                           :is_clean, :flags, :now, :now)""",
                {**row, "now": now},
            )
        else:
            self.conn.execute(
                "UPDATE listings SET price = ?, last_seen = ? WHERE id = ?",
                (row["price"], now, row["id"]),
            )
        self.conn.commit()
        return is_new, previous_price

    def market_prices(self, model: str, variant: Optional[int], days: int) -> List[int]:
        """Цены чистых объявлений этой связки за окно — основа для медианы."""
        since = int(time.time()) - days * 86400
        if variant is None:
            cur = self.conn.execute(
                """SELECT price FROM listings
                   WHERE model = ? AND variant IS NULL AND is_clean = 1 AND first_seen >= ?""",
                (model, since),
            )
        else:
            cur = self.conn.execute(
                """SELECT price FROM listings
                   WHERE model = ? AND variant = ? AND is_clean = 1 AND first_seen >= ?""",
                (model, variant, since),
            )
        return [r["price"] for r in cur.fetchall()]

    def index_summary(self, days: int, min_sample: int) -> List[sqlite3.Row]:
        since = int(time.time()) - days * 86400
        return self.conn.execute(
            """SELECT category, model, variant, COUNT(*) AS n,
                      MIN(price) AS lo, MAX(price) AS hi
               FROM listings
               WHERE is_clean = 1 AND first_seen >= ? AND model IS NOT NULL
               GROUP BY model, variant
               HAVING n >= ?
               ORDER BY category, model, variant""",
            (since, min_sample),
        ).fetchall()

    def counts(self) -> dict:
        row = self.conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(is_clean) AS clean,
                      COUNT(DISTINCT model) AS models
               FROM listings"""
        ).fetchone()
        return {"total": row["total"] or 0, "clean": row["clean"] or 0, "models": row["models"] or 0}

    # -------------------------------------------------------------- пользователи

    def upsert_user(self, user_id: int, username: str = "") -> User:
        self.conn.execute(
            """INSERT INTO users (user_id, username, created_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username = excluded.username""",
            (user_id, username, int(time.time())),
        )
        self.conn.commit()
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> Optional[User]:
        row = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return User(
            user_id=row["user_id"], username=row["username"] or "",
            active=bool(row["active"]), price_min=row["price_min"], price_max=row["price_max"],
            models=row["models"] or "",
            categories=row["categories"] if row["categories"] is not None else "iphone,watch",
            min_discount=row["min_discount"],
            min_battery=row["min_battery"],
        )

    def active_users(self) -> List[User]:
        rows = self.conn.execute("SELECT user_id FROM users WHERE active = 1").fetchall()
        return [u for u in (self.get_user(r["user_id"]) for r in rows) if u]

    def update_user(self, user_id: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE users SET {sets} WHERE user_id = ?", (*fields.values(), user_id)
        )
        self.conn.commit()

    # -------------------------------------------------------------- дедупликация

    def was_sent(self, user_id: int, listing_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sent WHERE user_id = ? AND listing_id = ?", (user_id, listing_id)
        ).fetchone() is not None

    def mark_sent(self, user_id: int, listing_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO sent VALUES (?, ?, ?)",
            (user_id, listing_id, int(time.time())),
        )
        self.conn.commit()

    def recent_sent(self, user_id: int, limit: int = 10) -> List[sqlite3.Row]:
        return self.conn.execute(
            """SELECT l.* FROM sent s JOIN listings l ON l.id = s.listing_id
               WHERE s.user_id = ? ORDER BY s.sent_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()

    # -------------------------------------------------------------- сохранённые

    def save_listing(self, user_id: int, listing_id: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO saved VALUES (?, ?, ?)",
                          (user_id, listing_id, int(time.time())))
        self.conn.commit()

    def unsave_listing(self, user_id: int, listing_id: str) -> None:
        self.conn.execute("DELETE FROM saved WHERE user_id = ? AND listing_id = ?",
                          (user_id, listing_id))
        self.conn.commit()

    def is_saved(self, user_id: int, listing_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM saved WHERE user_id = ? AND listing_id = ?", (user_id, listing_id)
        ).fetchone() is not None

    def saved_listings(self, user_id: int, limit: int = 30) -> List[sqlite3.Row]:
        return self.conn.execute(
            """SELECT l.*, s.saved_at FROM saved s JOIN listings l ON l.id = s.listing_id
               WHERE s.user_id = ? ORDER BY s.saved_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()

    def saved_count(self, user_id: int) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM saved WHERE user_id = ?",
                                (user_id,)).fetchone()
        return row["n"] if row else 0

    def get_listing(self, listing_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM listings WHERE id = ?",
                                 (listing_id,)).fetchone()

    # -------------------------------------------------------------- meta

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()
