---
name: price-fair-value-alert
description: Evaluates e-commerce pricing against historical trends to detect fake discounts, filter marketplace outlier spikes, compute empirical percentile ratings, and manage price-drop watchlists.
---

# Price Fair-Value Alert Skill

Use this skill when a user asks to check if a product price is a genuine deal, analyze price history, detect deceptive MRP discounts, or monitor prices for automated drop alerts.

## Tools Reference

The `price-fair-value` MCP server provides 8 tools:

| Tool | Purpose | Key Arguments |
| :--- | :--- | :--- |
| `evaluate_price` | Resolves product, fetches history, stores points, and computes a fair-value verdict | `url_or_query` (str), `current_price` (optional float), `currency` (str, default "INR") |
| `get_price_history` | Retrieves stored price points, statistics (min, max, median, mean), and recent records | `product_slug` (str), `days` (int, default 180) |
| `watch_product` | Adds a product to the active watchlist with alert triggers | `url_or_query` (str), `target_price` (optional float), `alert_on_deal` ("great_deal" or "good_deal") |
| `unwatch_product` | Removes a product from the watchlist | `product_slug` (str) |
| `list_watchlist` | Displays all actively monitored products, target prices, and latest known rates | *None* |
| `check_watchlist_alerts` | Scrapes/refreshes watched items, evaluates drop criteria, and triggers notifications | *None* |
| `record_custom_observation` | Manually logs an offline or newly discovered price point into the database | `product_slug` (str), `price` (float), `currency` (str), `retailer` (str) |
| `evaluate_custom_series` | Evaluates raw price lists from international sites (Keepa, CamelCamelCamel, BestBuy) | `product_name` (str), `current_price` (float), `history_prices` (list of float), `currency` (str), `mrp` (optional float) |

---

## Workflows

### 1. Evaluating Indian Retailer URLs (Amazon.in / Flipkart / PriceHistory.app)
When a user provides an Amazon India, Flipkart, or PriceHistory link:
1. Call `evaluate_price(url_or_query="<URL>")`.
2. Inspect the returned verdict:
   - `🟢 Great deal — near historical low`: The price is within 3% of the historical low or in the ≥80th percentile.
   - `🟢 Good deal — meaningfully below typical price`: Cheaper than 60–79% of historical prices.
   - `🟡 Fair — roughly the going rate`: Price is within normal typical range (40–59th percentile).
   - `🟠 Not great — above typical price`: Price is elevated (20–39th percentile).
   - `🔴 Bad deal — near historical high`: Near the peak of historical prices (<20th percentile).
3. Check for the `⚠️ DECEPTIVE MRP WARNING`. If present, highlight to the user that the seller's claimed discount (e.g. "40% off") is misleading because the typical selling price is far lower than the inflated MRP.

### 2. Evaluating International & Custom Price Series (Amazon US, Keepa, CamelCamelCamel)
When working with non-Indian retailers (e.g. Amazon.com, BestBuy, eBay):
1. Extract historical price points from search results, web pages, or tools.
2. Call `evaluate_custom_series(product_name="...", current_price=..., history_prices=[...], currency="USD", mrp=...)`.
3. The engine will run IQR outlier filtering and compute the empirical mid-rank percentile without requiring web scraping.

### 3. Setting Up Watchlists & Automated Alerts
When a user wants to monitor a product for price drops:
1. Add the item:
   ```python
   watch_product(url_or_query="https://amazon.in/dp/...", target_price=24999.0)
   # Or alert whenever it becomes a "great_deal"
   watch_product(url_or_query="https://amazon.in/dp/...", alert_on_deal="great_deal")
   ```
2. Periodically check alerts (e.g. scheduled via cron):
   ```python
   check_watchlist_alerts()
   ```
   If target criteria or rating thresholds are met, the tool returns formatted alerts with savings details and automatically updates `last_notified_at`.

### 4. Outlier Handling Note
The statistical engine automatically applies an Interquartile Range (IQR) filter with median fallback `[0.3 * median, 2.5 * median]` when sample size ≥ 10. Marketplace seller price gouges (e.g., an ₹83,999 spike on a ₹28,000 item) are filtered out so they do not distort the true historical average or maximum.
