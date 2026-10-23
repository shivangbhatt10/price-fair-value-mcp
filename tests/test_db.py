from __future__ import annotations

import sqlite3
import pytest

from price_fair_value.models import PricePoint, ProductMetadata, WatchlistItem
from price_fair_value.db import (
    get_db_path,
    init_db,
    upsert_product,
    get_product,
    list_products,
    save_price_points_batch,
    get_price_history,
    upsert_watch,
    remove_watch,
    list_watches,
    mark_watch_notified,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Ensure every test runs with an isolated database file via PRICE_DB_PATH."""
    test_db = tmp_path / "test.db"
    monkeypatch.setenv("PRICE_DB_PATH", str(test_db))
    init_db()
    return test_db


class TestDatabaseInitAndMigration:
    def test_init_db_creates_tables(self, setup_test_db):
        """Verify that all required tables and indexes are created by init_db."""
        conn = sqlite3.connect(str(setup_test_db))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "products" in tables
        assert "price_points" in tables
        assert "watchlist" in tables

    def test_migration_from_legacy_schema(self, tmp_path, monkeypatch):
        """Simulate an older schema without day_bucket, offer_price, or deal_type and verify safe migration."""
        legacy_db = tmp_path / "legacy.db"
        monkeypatch.setenv("PRICE_DB_PATH", str(legacy_db))

        conn = sqlite3.connect(str(legacy_db))
        # Create older partial schema
        conn.execute(
            """
            CREATE TABLE price_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_slug TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                retailer TEXT NOT NULL,
                source TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO price_points (product_slug, price, currency, retailer, source, recorded_at)
            VALUES ('item-1', 999.0, 'INR', 'amazon.in', 'pricehistory.app', '2024-03-15T10:00:00')
            """
        )
        conn.commit()
        conn.close()

        # Run init_db which should perform migrations safely
        init_db()

        conn = sqlite3.connect(str(legacy_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM price_points WHERE product_slug = 'item-1'").fetchone()
        conn.close()

        assert row["day_bucket"] == "2024-03-15"
        assert "offer_price" in row.keys()
        assert "deal_type" in row.keys()


class TestProductOperations:
    def test_upsert_and_get_product(self):
        """Insert product and update existing product metadata."""
        meta = ProductMetadata(
            product_slug="sony-wh1000xm5",
            canonical_url="https://amazon.in/dp/B09XS7JWHH",
            title="Sony WH-1000XM5 Wireless Headphones",
            retailer="amazon.in",
            currency="INR",
            mrp=34990.0,
            last_checked="2024-05-01T12:00:00",
        )
        upsert_product(meta)

        fetched = get_product("sony-wh1000xm5")
        assert fetched is not None
        assert fetched.product_slug == "sony-wh1000xm5"
        assert fetched.title == "Sony WH-1000XM5 Wireless Headphones"
        assert fetched.mrp == 34990.0

        # Update metadata
        updated_meta = ProductMetadata(
            product_slug="sony-wh1000xm5",
            canonical_url="https://amazon.in/dp/B09XS7JWHH",
            title="Sony WH-1000XM5 (Black)",
            retailer="amazon.in",
            currency="INR",
            mrp=32990.0,
            last_checked="2024-05-02T12:00:00",
        )
        upsert_product(updated_meta)

        refetched = get_product("sony-wh1000xm5")
        assert refetched.title == "Sony WH-1000XM5 (Black)"
        assert refetched.mrp == 32990.0

    def test_get_nonexistent_product(self):
        """Nonexistent slug should return None."""
        assert get_product("does-not-exist") is None

    def test_list_products_aggregation(self):
        """Check list_products returns observations count, min_price, max_price."""
        meta = ProductMetadata(
            product_slug="mouse-123",
            canonical_url="https://amazon.in/dp/M123",
            title="Wireless Mouse",
            retailer="amazon.in",
            currency="INR",
            mrp=1999.0,
            last_checked="2024-05-01T10:00:00",
        )
        upsert_product(meta)

        points = [
            PricePoint(price=999.0, recorded_at="2024-05-01T10:00:00"),
            PricePoint(price=799.0, recorded_at="2024-05-02T10:00:00"),
        ]
        save_price_points_batch("mouse-123", points)

        summary_list = list_products()
        assert len(summary_list) == 1
        item = summary_list[0]
        assert item["product_slug"] == "mouse-123"
        assert item["observations"] == 2
        assert item["min_price"] == 799.0
        assert item["max_price"] == 999.0


class TestPricePointsBatchAndIdempotency:
    def test_batch_insertion_and_idempotency(self):
        """Inserting same day/price twice must only insert 1 row (INSERT OR IGNORE)."""
        slug = "kindle-paperwhite"

        points = [
            PricePoint(price=11999.0, recorded_at="2024-04-10T08:00:00"),
            PricePoint(price=11999.0, recorded_at="2024-04-10T18:00:00"),  # Duplicate day_bucket (2024-04-10) and price
            PricePoint(price=10999.0, recorded_at="2024-04-11T09:00:00"),
        ]

        # First run: 2 unique day/price rows should be inserted
        inserted = save_price_points_batch(slug, points)
        assert inserted == 2

        # Second run: inserting exact same batch should insert 0 new rows
        inserted_again = save_price_points_batch(slug, points)
        assert inserted_again == 0

        # Verify history ordering and content
        history = get_price_history(slug)
        assert len(history) == 2
        assert history[0].recorded_at == "2024-04-10T08:00:00"
        assert history[0].price == 11999.0
        assert history[1].recorded_at == "2024-04-11T09:00:00"
        assert history[1].price == 10999.0


class TestWatchlistOperations:
    def test_watchlist_lifecycle(self):
        """Test adding, listing, updating notification time, and removing watches."""
        watch_active = WatchlistItem(
            product_slug="macbook-air-m2",
            url_or_query="https://amazon.in/dp/B0B3C1Y8H9",
            target_price=89990.0,
            alert_on_deal="great_deal",
            created_at="2024-01-01T00:00:00",
            is_active=True,
        )
        watch_inactive = WatchlistItem(
            product_slug="ipad-air",
            url_or_query="https://amazon.in/dp/IPADAIR",
            target_price=49990.0,
            alert_on_deal="good_deal",
            created_at="2024-01-02T00:00:00",
            is_active=False,
        )

        upsert_watch(watch_active)
        upsert_watch(watch_inactive)

        # Active only check
        active_watches = list_watches(active_only=True)
        assert len(active_watches) == 1
        assert active_watches[0].product_slug == "macbook-air-m2"
        assert active_watches[0].target_price == 89990.0
        assert active_watches[0].is_active is True

        # All watches check
        all_watches = list_watches(active_only=False)
        assert len(all_watches) == 2

        # Mark notified
        notif_time = "2024-05-15T15:30:00"
        mark_watch_notified("macbook-air-m2", notif_time)
        updated_active = list_watches(active_only=True)
        assert updated_active[0].last_notified_at == notif_time

        # Remove watch
        assert remove_watch("macbook-air-m2") is True
        assert remove_watch("macbook-air-m2") is False  # Second removal returns False

        remaining_active = list_watches(active_only=True)
        assert len(remaining_active) == 0

        remaining_all = list_watches(active_only=False)
        assert len(remaining_all) == 1
        assert remaining_all[0].product_slug == "ipad-air"
