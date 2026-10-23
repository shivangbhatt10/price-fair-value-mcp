from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock
import pytest

from scrapling.fetchers import Fetcher

from price_fair_value.providers.pricehistory_app import (
    extract_listing_slug,
    decrypt_embedded_dataset,
    resolve_to_pricehistory_url,
    fetch_and_parse,
)


def _create_synthetic_encrypted_script(payload: dict, key: str = "TestSecretKey123") -> str:
    """Helper to generate a valid XOR+base64 encrypted PriceHistory script block."""
    raw_str = json.dumps(payload)
    xored = "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(raw_str))
    b64 = base64.b64encode(xored.encode("latin1")).decode("ascii")
    return f'var PagePriceHistoryDataSet = "{b64}"; var CachedKey = \'{key}\';'


class TestExtractListingSlug:
    def test_deduplication_across_two_slugs_sharing_same_8char_id(self):
        """Two differing URLs/slugs sharing the same 8-char suffix deduplicate to the same slug."""
        url_a = "https://pricehistory.app/p/sony-wh-1000xm5-noise-cancelling-1n4hLvyp"
        code_b = "sony-headphones-xm5-1n4hLvyp"

        slug_a = extract_listing_slug(url_a)
        slug_b = extract_listing_slug(code_b)

        assert slug_a == "1n4hlvyp"
        assert slug_b == "1n4hlvyp"
        assert slug_a == slug_b

    def test_standalone_8char_code(self):
        """Pure 8-character alphanumeric code is lowercased."""
        assert extract_listing_slug("1N4hLvyp") == "1n4hlvyp"

    def test_fallback_slugify(self):
        """Non-8-char strings fallback to hyphen-slugified text."""
        assert extract_listing_slug("https://amazon.in/dp/B09XS7JWHH") == "https-amazon-in-dp-b09xs7jwhh"


class TestDecryptEmbeddedDataset:
    def test_decrypt_synthetic_payload(self):
        """Verify XOR decryption reproduces exact dictionary payload."""
        sample_payload = {
            "Code": "1n4hLvyp",
            "Price": {"Price": 19989, "MRP": 34990, "MinPrice": 19989, "MaxPrice": 83999},
            "History": {
                "Price": [
                    {"x": "2024-01-01 10:00:00", "y": 24990},
                    {"x": "2024-02-01 12:00:00", "y": 19989},
                ]
            },
        }

        script_text = _create_synthetic_encrypted_script(sample_payload)
        decrypted = decrypt_embedded_dataset(script_text)

        assert decrypted is not None
        assert decrypted["Code"] == "1n4hLvyp"
        assert decrypted["Price"]["Price"] == 19989
        assert decrypted["Price"]["MRP"] == 34990
        assert len(decrypted["History"]["Price"]) == 2

    def test_decrypt_invalid_script_returns_none(self):
        """Missing PagePriceHistoryDataSet or CachedKey returns None."""
        assert decrypt_embedded_dataset("var randomScript = 123;") is None
        assert decrypt_embedded_dataset('var PagePriceHistoryDataSet = "abc";') is None


class TestResolveUrl:
    def test_pricehistory_url_returns_directly(self, monkeypatch):
        """Existing pricehistory.app URL should not trigger Fetcher.post."""
        mock_post = MagicMock()
        monkeypatch.setattr(Fetcher, "post", mock_post)

        direct_url = "https://pricehistory.app/p/sony-wh-1000xm5-1n4hLvyp"
        resolved = resolve_to_pricehistory_url(direct_url)

        assert resolved == direct_url
        mock_post.assert_not_called()

    def test_resolve_amazon_url_mocked_post(self, monkeypatch):
        """Resolving an Amazon product URL queries the search API and constructs the URL."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.body = json.dumps(
            {"status": True, "code": "sony-wh-1000xm5-1n4hLvyp", "name": "Sony WH-1000XM5"}
        ).encode("utf-8")

        mock_post = MagicMock(return_value=mock_response)
        monkeypatch.setattr(Fetcher, "post", mock_post)

        amazon_url = "https://www.amazon.in/dp/B09XS7JWHH"
        resolved = resolve_to_pricehistory_url(amazon_url)

        assert resolved == "https://pricehistory.app/p/sony-wh-1000xm5-1n4hLvyp"
        mock_post.assert_called_once_with(
            "https://pricehistory.app/api/search",
            json={"url": amazon_url},
            timeout=15,
        )

    def test_resolve_failure_raises_value_error(self, monkeypatch):
        """Failed search response raises ValueError."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.body = json.dumps({"status": False, "message": "Not found"}).encode("utf-8")

        monkeypatch.setattr(Fetcher, "post", MagicMock(return_value=mock_response))

        with pytest.raises(ValueError, match="could not find match"):
            resolve_to_pricehistory_url("https://www.amazon.in/dp/B00UNKNOWN")


class TestFetchAndParse:
    def test_fetch_and_parse_embedded_dataset(self, monkeypatch):
        """Fetch and parse via embedded XOR encrypted dataset."""
        payload = {
            "Code": "1n4hLvyp",
            "Price": {"Price": 19989, "MRP": 34990, "MinPrice": 19989, "MaxPrice": 83999},
            "History": {
                "Price": [
                    {"x": "2024-01-01 10:00:00", "y": 24990},
                    {"x": "2024-02-01 12:00:00", "y": 19989},
                ]
            },
        }
        script_content = _create_synthetic_encrypted_script(payload)

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Sony WH-1000XM5 Price in India</title></head>
        <body>
            <h1>Sony WH-1000XM5 Noise Cancelling Headphones</h1>
            <a href="#">Buy @ Amazon.in</a>
            <script>{script_content}</script>
        </body>
        </html>
        """

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.body = html.encode("utf-8")

        monkeypatch.setattr(Fetcher, "get", MagicMock(return_value=mock_response))

        target_url = "https://pricehistory.app/p/sony-wh-1000xm5-1n4hLvyp"
        meta, current_price, history = fetch_and_parse(target_url)

        assert meta.product_slug == "1n4hlvyp"
        assert meta.title == "Sony WH-1000XM5 Noise Cancelling Headphones"
        assert meta.retailer == "amazon.in"
        assert meta.currency == "INR"
        assert meta.mrp == 34990.0
        assert current_price == 19989.0
        assert len(history) == 2
        assert history[0].price == 24990.0
        assert history[1].price == 19989.0

    def test_fetch_and_parse_meta_description_fallback(self, monkeypatch):
        """Fallback to meta description when embedded dataset is absent."""
        desc = (
            "Sony WH-1000XM5 Price in India on 23 October 2026 is ₹19,989. "
            "Lowest Price: ₹19,989 | Average Price: ₹24,500 | Highest Price: ₹29,990 | MRP: ₹34,990."
        )

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Sony WH-1000XM5 Price</title>
            <meta name="description" content="{desc}">
        </head>
        <body>
            <h1>Sony WH-1000XM5 Wireless Headphones</h1>
            <div>Buy @ Flipkart</div>
        </body>
        </html>
        """

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.body = html.encode("utf-8")

        monkeypatch.setattr(Fetcher, "get", MagicMock(return_value=mock_response))

        target_url = "https://pricehistory.app/p/sony-wh-1000xm5-1n4hLvyp"
        meta, current_price, history = fetch_and_parse(target_url)

        assert meta.product_slug == "1n4hlvyp"
        assert meta.retailer == "flipkart.com"
        assert meta.title == "Sony WH-1000XM5 Wireless Headphones"
        assert current_price == 19989.0
        assert meta.mrp == 34990.0
        assert len(history) >= 3
        # Check prices present in history
        history_prices = [p.price for p in history]
        assert 19989.0 in history_prices
        assert 24500.0 in history_prices
        assert 29990.0 in history_prices
