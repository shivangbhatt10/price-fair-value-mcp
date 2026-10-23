from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
from typing import Iterator

from price_fair_value.models import PricePoint, ProductMetadata, WatchlistItem


def get_db_path() -> Path:
    """
    Get the SQLite database path.
    Reads os.environ.get('PRICE_DB_PATH') on every call (allowing test monkeypatching),
    defaulting to <repo_root>/data/price_history.db.
    """
    env_path = os.environ.get("PRICE_DB_PATH")
    if env_path:
        return Path(env_path).resolve()
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / "data" / "price_history.db").resolve()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Context manager for SQLite connections with Row factory and auto-close."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """
    Create parent directories and initialize all 3 tables:
    - products
    - price_points (with unique constraint and safe migrations for older schemas)
    - watchlist
    """
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        with conn:
            cursor = conn.cursor()

            # 1. products table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    product_slug TEXT PRIMARY KEY,
                    canonical_url TEXT,
                    title TEXT,
                    retailer TEXT,
                    currency TEXT,
                    mrp REAL,
                    last_checked TEXT
                )
                """
            )

            # 2. price_points table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS price_points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_slug TEXT NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL,
                    retailer TEXT NOT NULL,
                    source TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    day_bucket TEXT NOT NULL,
                    offer_price REAL,
                    deal_type TEXT,
                    UNIQUE(product_slug, retailer, source, day_bucket, price)
                )
                """
            )

            # Safely migrate existing price_points table if older columns are missing
            cursor.execute("PRAGMA table_info(price_points)")
            existing_cols = {row["name"] for row in cursor.fetchall()}

            if existing_cols:
                if "day_bucket" not in existing_cols:
                    cursor.execute("ALTER TABLE price_points ADD COLUMN day_bucket TEXT")
                    cursor.execute(
                        "UPDATE price_points SET day_bucket = substr(recorded_at, 1, 10) WHERE day_bucket IS NULL"
                    )
                if "offer_price" not in existing_cols:
                    cursor.execute("ALTER TABLE price_points ADD COLUMN offer_price REAL")
                if "deal_type" not in existing_cols:
                    cursor.execute("ALTER TABLE price_points ADD COLUMN deal_type TEXT")

            # Unique index to enforce uniqueness even if table was created without inline constraint
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_price_points_dedup
                ON price_points(product_slug, retailer, source, day_bucket, price)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_price_points_slug_recorded
                ON price_points(product_slug, recorded_at)
                """
            )

            # 3. watchlist table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    product_slug TEXT PRIMARY KEY,
                    url_or_query TEXT NOT NULL,
                    target_price REAL,
                    alert_on_deal TEXT NOT NULL DEFAULT 'great_deal',
                    created_at TEXT NOT NULL,
                    last_notified_at TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )


def upsert_product(meta: ProductMetadata) -> None:
    """Insert or update a product entry in the products table."""
    with get_connection() as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO products (
                    product_slug, canonical_url, title, retailer, currency, mrp, last_checked
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_slug) DO UPDATE SET
                    canonical_url = excluded.canonical_url,
                    title = excluded.title,
                    retailer = excluded.retailer,
                    currency = excluded.currency,
                    mrp = excluded.mrp,
                    last_checked = excluded.last_checked
                """,
                (
                    meta.product_slug,
                    meta.canonical_url,
                    meta.title,
                    meta.retailer,
                    meta.currency,
                    meta.mrp,
                    meta.last_checked,
                ),
            )


def save_price_points_batch(slug: str, points: list[PricePoint]) -> int:
    """
    Save a batch of PricePoint items using INSERT OR IGNORE inside a single transaction.
    Returns the count of newly inserted rows.
    """
    if not points:
        return 0

    inserted_count = 0
    with get_connection() as conn:
        with conn:
            cursor = conn.cursor()
            for pt in points:
                day_bucket = pt.recorded_at[:10] if pt.recorded_at else ""
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO price_points (
                        product_slug, price, currency, retailer, source, recorded_at, day_bucket, offer_price, deal_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slug,
                        float(pt.price),
                        pt.currency,
                        pt.retailer,
                        pt.source,
                        pt.recorded_at,
                        day_bucket,
                        pt.offer_price,
                        pt.deal_type,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted_count += cursor.rowcount

    return inserted_count


def get_price_history(slug: str, days: int = 3650) -> list[PricePoint]:
    """
    Retrieve historical price points for a product slug, ordered by recorded_at ASC.
    If days < 3650, filters within the trailing window from current UTC time.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        if days and days < 3650:
            cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            cursor.execute(
                """
                SELECT price, recorded_at, currency, retailer, source, offer_price, deal_type
                FROM price_points
                WHERE product_slug = ? AND recorded_at >= ?
                ORDER BY recorded_at ASC
                """,
                (slug, cutoff),
            )
        else:
            cursor.execute(
                """
                SELECT price, recorded_at, currency, retailer, source, offer_price, deal_type
                FROM price_points
                WHERE product_slug = ?
                ORDER BY recorded_at ASC
                """,
                (slug,),
            )
        rows = cursor.fetchall()
        return [
            PricePoint(
                price=float(row["price"]),
                recorded_at=row["recorded_at"],
                currency=row["currency"],
                retailer=row["retailer"],
                source=row["source"],
                offer_price=float(row["offer_price"]) if row["offer_price"] is not None else None,
                deal_type=row["deal_type"],
            )
            for row in rows
        ]


def get_product(slug: str) -> ProductMetadata | None:
    """Retrieve metadata for a product by slug, or None if not found."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT product_slug, canonical_url, title, retailer, currency, mrp, last_checked
            FROM products
            WHERE product_slug = ?
            """,
            (slug,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ProductMetadata(
            product_slug=row["product_slug"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            retailer=row["retailer"],
            currency=row["currency"],
            mrp=float(row["mrp"]) if row["mrp"] is not None else None,
            last_checked=row["last_checked"],
        )


def list_products() -> list[dict]:
    """
    List all products with summary statistics:
    observations count, min_price, max_price, and last_checked.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                p.product_slug,
                p.title,
                p.currency,
                p.retailer,
                COUNT(pp.id) AS observations,
                MIN(pp.price) AS min_price,
                MAX(pp.price) AS max_price,
                p.last_checked
            FROM products p
            LEFT JOIN price_points pp ON p.product_slug = pp.product_slug
            GROUP BY p.product_slug
            ORDER BY p.product_slug ASC
            """
        )
        rows = cursor.fetchall()
        return [
            {
                "product_slug": row["product_slug"],
                "title": row["title"],
                "currency": row["currency"],
                "retailer": row["retailer"],
                "observations": int(row["observations"]),
                "min_price": float(row["min_price"]) if row["min_price"] is not None else None,
                "max_price": float(row["max_price"]) if row["max_price"] is not None else None,
                "last_checked": row["last_checked"],
            }
            for row in rows
        ]


def upsert_watch(item: WatchlistItem) -> None:
    """Insert or update a watchlist item."""
    created_at = item.created_at or datetime.utcnow().isoformat()
    is_active_int = 1 if item.is_active else 0
    with get_connection() as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO watchlist (
                    product_slug, url_or_query, target_price, alert_on_deal, created_at, last_notified_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_slug) DO UPDATE SET
                    url_or_query = excluded.url_or_query,
                    target_price = excluded.target_price,
                    alert_on_deal = excluded.alert_on_deal,
                    created_at = excluded.created_at,
                    last_notified_at = excluded.last_notified_at,
                    is_active = excluded.is_active
                """,
                (
                    item.product_slug,
                    item.url_or_query,
                    item.target_price,
                    item.alert_on_deal,
                    created_at,
                    item.last_notified_at,
                    is_active_int,
                ),
            )


def remove_watch(slug: str) -> bool:
    """Remove a watchlist item by slug. Returns True if removed, False otherwise."""
    with get_connection() as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM watchlist WHERE product_slug = ?", (slug,))
            return cursor.rowcount > 0


def list_watches(active_only: bool = True) -> list[WatchlistItem]:
    """List watchlist items. If active_only is True, filters to is_active == 1."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if active_only:
            cursor.execute(
                """
                SELECT product_slug, url_or_query, target_price, alert_on_deal, created_at, last_notified_at, is_active
                FROM watchlist
                WHERE is_active = 1
                ORDER BY created_at ASC
                """
            )
        else:
            cursor.execute(
                """
                SELECT product_slug, url_or_query, target_price, alert_on_deal, created_at, last_notified_at, is_active
                FROM watchlist
                ORDER BY created_at ASC
                """
            )
        rows = cursor.fetchall()
        return [
            WatchlistItem(
                product_slug=row["product_slug"],
                url_or_query=row["url_or_query"],
                target_price=float(row["target_price"]) if row["target_price"] is not None else None,
                alert_on_deal=row["alert_on_deal"],
                created_at=row["created_at"],
                last_notified_at=row["last_notified_at"],
                is_active=bool(row["is_active"]),
            )
            for row in rows
        ]


def mark_watch_notified(slug: str, timestamp_iso: str) -> None:
    """Update last_notified_at timestamp for a watchlist item."""
    with get_connection() as conn:
        with conn:
            conn.execute(
                "UPDATE watchlist SET last_notified_at = ? WHERE product_slug = ?",
                (timestamp_iso, slug),
            )
