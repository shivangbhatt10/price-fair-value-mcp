from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from price_fair_value.db import (
    get_price_history as db_get_price_history,
    init_db,
    save_price_points_batch,
    upsert_product,
)
from price_fair_value.models import PricePoint, ProductMetadata
from price_fair_value.server import (
    check_watchlist_alerts,
    evaluate_custom_series,
    evaluate_price,
    get_price_history,
    list_watchlist,
    record_custom_observation,
    unwatch_product,
    watch_product,
)


@pytest.fixture(autouse=True)
def setup_test_server_db(tmp_path, monkeypatch):
    """Ensure tests run against an isolated SQLite database file."""
    test_db = tmp_path / "server_test.db"
    monkeypatch.setenv("PRICE_DB_PATH", str(test_db))
    init_db()
    return test_db


class TestServerTools:
    def test_evaluate_price_success_and_storage(self, monkeypatch):
        """Tool 1: evaluate_price fetches, saves to DB, and returns rich verdict."""
        mock_meta = ProductMetadata(
            product_slug="1n4hlvyp",
            canonical_url="https://pricehistory.app/p/sony-1n4hLvyp",
            title="Sony WH-1000XM5 Wireless Headphones",
            retailer="amazon.in",
            currency="INR",
            mrp=34990.0,
            last_checked="2024-05-01T10:00:00",
        )
        mock_history = [
            PricePoint(price=24990.0, recorded_at="2024-01-01T00:00:00"),
            PricePoint(price=22990.0, recorded_at="2024-02-01T00:00:00"),
            PricePoint(price=19989.0, recorded_at="2024-03-01T00:00:00"),
        ]

        monkeypatch.setattr(
            "price_fair_value.server.fetch_and_parse",
            MagicMock(return_value=(mock_meta, 19989.0, mock_history)),
        )

        report = evaluate_price("https://www.amazon.in/dp/B09XS7JWHH")

        assert "Sony WH-1000XM5 Wireless Headphones" in report
        assert "1n4hlvyp" in report
        assert "19,989.00" in report
        assert "34,990.00" in report
        assert "Statistical Summary" in report
        assert "DECEPTIVE MRP WARNING" in report

        # Verify points stored in DB
        db_points = db_get_price_history("1n4hlvyp")
        assert len(db_points) == 3

    def test_evaluate_price_error_handling(self, monkeypatch):
        """Tool 1: evaluate_price returns formatted error on scraper failure."""
        monkeypatch.setattr(
            "price_fair_value.server.fetch_and_parse",
            MagicMock(side_effect=ValueError("Product not found")),
        )
        report = evaluate_price("https://amazon.in/dp/B00UNKNOWN")
        assert "❌ **Error evaluating product**" in report
        assert "Product not found" in report

    def test_get_price_history_tool(self):
        """Tool 2: get_price_history retrieves stored records and summary."""
        from datetime import datetime, timedelta
        slug = "logitech-mx3"
        upsert_product(
            ProductMetadata(
                product_slug=slug,
                canonical_url="https://amazon.in/dp/MX3",
                title="Logitech MX Master 3S",
                retailer="amazon.in",
                currency="INR",
                mrp=9995.0,
            )
        )
        now = datetime.utcnow()
        points = [
            PricePoint(price=8995.0, recorded_at=(now - timedelta(days=20)).isoformat()),
            PricePoint(price=7995.0, recorded_at=(now - timedelta(days=5)).isoformat()),
        ]
        save_price_points_batch(slug, points)

        res = get_price_history(slug, days=180)
        assert "Logitech MX Master 3S" in res
        assert "**Total Observations**: 2" in res
        assert "7,995.00" in res

        # Nonexistent slug
        empty_res = get_price_history("unknown-slug")
        assert "No stored price history found" in empty_res

    def test_watch_and_unwatch_lifecycle(self, monkeypatch):
        """Tools 3 & 4: watch_product and unwatch_product lifecycle."""
        mock_meta = ProductMetadata(
            product_slug="pixel-8",
            canonical_url="https://flipkart.com/pixel-8",
            title="Google Pixel 8",
            retailer="flipkart.com",
            currency="INR",
            mrp=75999.0,
        )
        monkeypatch.setattr(
            "price_fair_value.server.fetch_and_parse",
            MagicMock(return_value=(mock_meta, 65999.0, [PricePoint(price=65999.0, recorded_at="2024-03-01T00:00:00")])),
        )

        # Watch product
        watch_res = watch_product("https://flipkart.com/pixel-8", target_price=60000.0)
        assert "Watching `pixel-8`" in watch_res
        assert "60,000.00" in watch_res

        # Unwatch product
        unwatch_res = unwatch_product("pixel-8")
        assert "Removed `pixel-8`" in unwatch_res

        # Second unwatch
        unwatch_again = unwatch_product("pixel-8")
        assert "was not found" in unwatch_again

    def test_list_watchlist_tool(self, monkeypatch):
        """Tool 5: list_watchlist shows table of watched items."""
        # When empty
        assert "currently empty" in list_watchlist()

        mock_meta = ProductMetadata(
            product_slug="mac-mini",
            canonical_url="",
            title="Apple Mac Mini M2",
            retailer="amazon.in",
            currency="INR",
            mrp=59900.0,
        )
        monkeypatch.setattr(
            "price_fair_value.server.fetch_and_parse",
            MagicMock(return_value=(mock_meta, 54900.0, [])),
        )
        watch_product("mac-mini", target_price=50000.0)

        listed = list_watchlist()
        assert "Active Price Watchlist" in listed
        assert "mac-mini" in listed
        assert "50,000.00" in listed

    def test_check_watchlist_alerts_tool(self, monkeypatch):
        """Tool 6: check_watchlist_alerts evaluates items and triggers drop notifications."""
        mock_meta = ProductMetadata(
            product_slug="iphone-15",
            canonical_url="https://amazon.in/dp/IPHONE15",
            title="Apple iPhone 15",
            retailer="amazon.in",
            currency="INR",
            mrp=79900.0,
        )
        # History showing median of 74,900
        history = [
            PricePoint(price=74900.0, recorded_at="2024-01-01T00:00:00"),
            PricePoint(price=74900.0, recorded_at="2024-02-01T00:00:00"),
            PricePoint(price=74900.0, recorded_at="2024-03-01T00:00:00"),
        ]

        # Seed watch item with target price 70,000
        monkeypatch.setattr(
            "price_fair_value.server.fetch_and_parse",
            MagicMock(return_value=(mock_meta, 74900.0, history)),
        )
        watch_product("https://amazon.in/dp/IPHONE15", target_price=70000.0)

        # First alert check: Price drops to 68,000 (triggers target price alert)
        monkeypatch.setattr(
            "price_fair_value.server.fetch_and_parse",
            MagicMock(return_value=(mock_meta, 68000.0, history + [PricePoint(price=68000.0, recorded_at="2024-04-01T00:00:00")])),
        )
        alert_res = check_watchlist_alerts()
        assert "Price Alerts Triggered" in alert_res
        assert "Apple iPhone 15" in alert_res
        assert "₹68,000.00" in alert_res

        # Second check: When price is above target
        monkeypatch.setattr(
            "price_fair_value.server.fetch_and_parse",
            MagicMock(return_value=(mock_meta, 75000.0, history)),
        )
        no_alert_res = check_watchlist_alerts()
        assert "No price drops or target criteria met" in no_alert_res

    def test_record_custom_observation_tool(self):
        """Tool 7: record_custom_observation saves manual price points."""
        res = record_custom_observation("samsung-s24", 74999.0, currency="INR", retailer="croma")
        assert "Recorded observation for `samsung-s24`" in res
        assert "74,999.00" in res

        # Verify DB
        history = db_get_price_history("samsung-s24")
        assert len(history) == 1
        assert history[0].price == 74999.0
        assert history[0].retailer == "croma"

    def test_evaluate_custom_series_tool(self):
        """Tool 8: evaluate_custom_series runs statistical engine on caller-provided price lists."""
        series = [1799.0, 1749.0, 1699.0, 1649.0, 1599.0, 1549.0, 1499.0]
        res = evaluate_custom_series(
            product_name="Dell XPS 15",
            current_price=1499.0,
            history_prices=series,
            currency="USD",
            mrp=1999.0,
        )

        assert "Dell XPS 15" in res
        assert "USD 1,499.00" in res
        assert "USD 1,999.00" in res
        assert "Statistical Summary" in res

        # Error on empty list
        err_res = evaluate_custom_series("XPS", 1000.0, [])
        assert "Error" in err_res
