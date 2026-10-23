from __future__ import annotations

from datetime import datetime, timedelta
import os
import sys
from typing import Sequence

from mcp.server.fastmcp import FastMCP

from price_fair_value.db import (
    get_price_history as db_get_price_history,
    get_product,
    init_db,
    list_watches,
    mark_watch_notified,
    remove_watch,
    save_price_points_batch,
    upsert_product,
    upsert_watch,
)
from price_fair_value.engine import compute_fair_value_verdict
from price_fair_value.models import PricePoint, ProductMetadata, WatchlistItem
from price_fair_value.providers.pricehistory_app import (
    extract_listing_slug,
    fetch_and_parse,
)

# Initialize FastMCP Server
mcp = FastMCP("price-fair-value")


def _log(msg: str) -> None:
    """Log strictly to stderr so stdout JSON-RPC protocol is never corrupted."""
    sys.stderr.write(f"[price-fair-value-mcp] {msg}\n")
    sys.stderr.flush()


@mcp.tool()
def evaluate_price(
    url_or_query: str,
    current_price: float | None = None,
    currency: str = "INR",
) -> str:
    """
    Evaluates product price against historical pricing using PriceHistory.app.
    Saves product metadata and price points to DB, computes fair-value verdict,
    and returns a rich Markdown verdict report.
    """
    init_db()
    try:
        meta, scraped_price, history = fetch_and_parse(url_or_query)
    except Exception as e:
        _log(f"Failed to fetch_and_parse for {url_or_query}: {e}")
        return f"❌ **Error evaluating product**: Could not retrieve price history for `{url_or_query}`: {e}"

    # Use provided current_price if given, else scraped current price
    eval_price = float(current_price) if current_price is not None and current_price > 0 else scraped_price

    # Persist product and history batch to database
    try:
        upsert_product(meta)
        if history:
            save_price_points_batch(meta.product_slug, history)
    except Exception as e:
        _log(f"Database save error for {meta.product_slug}: {e}")

    # Compute verdict using trailing 180-day window
    verdict = compute_fair_value_verdict(
        current_price=eval_price,
        history=history,
        mrp=meta.mrp,
        window_days=180,
    )

    # Format Markdown report
    lines: list[str] = [
        f"### {verdict.verdict}",
        f"**Product**: {meta.title}",
        f"- **Tracking Slug**: `{meta.product_slug}`",
        f"- **Retailer**: {meta.retailer} | **Canonical URL**: {meta.canonical_url}",
        f"- **Current Price**: {currency} {verdict.current_price:,.2f}",
    ]

    if meta.mrp:
        lines.append(f"- **Claimed MRP**: {currency} {meta.mrp:,.2f}")

    lines.extend([
        "",
        "#### Statistical Summary (Trailing 180 Days)",
        f"- **Deal Rating**: `{verdict.deal_rating}`",
        f"- **Historical Percentile**: {verdict.percentile}% (cheaper than {verdict.percentile}% of historical points)",
        f"- **True Discount (vs Median)**: {verdict.true_discount_pct}%",
        f"- **180-Day Low / Median / High**: {currency} {verdict.min_price:,.2f} / {currency} {verdict.median_price:,.2f} / {currency} {verdict.max_price:,.2f}",
        f"- **Confidence**: `{verdict.confidence}` ({verdict.sample_size} observations over {verdict.observation_span_days:.0f} days)",
    ])

    if verdict.is_deceptive_mrp:
        lines.extend([
            "",
            "⚠️ **DECEPTIVE MRP WARNING**: The seller claims a discount of "
            f"**{verdict.fake_mrp_discount_pct}%**, but the true discount against typical going rates is only **{verdict.true_discount_pct}%**. "
            "The advertised MRP appears inflated to make the deal look deceptively attractive.",
        ])

    lines.extend([
        "",
        f"> **Watchlist Tip**: Track future price drops using `watch_product(url_or_query='{meta.product_slug}', target_price={verdict.median_price})`.",
    ])

    return "\n".join(lines)


@mcp.tool()
def get_price_history(product_slug: str, days: int = 180) -> str:
    """
    Retrieves stored price history from the SQLite database for a product slug
    over the trailing number of days, returning statistics and recent price points.
    """
    init_db()
    slug = extract_listing_slug(product_slug)
    history = db_get_price_history(slug, days=days)
    meta = get_product(slug)

    if not history:
        return f"ℹ️ No stored price history found for `{slug}` in the trailing {days} days."

    prices = [p.price for p in history]
    title = meta.title if meta else slug
    currency = meta.currency if meta else history[0].currency

    min_p = min(prices)
    max_p = max(prices)
    mean_p = sum(prices) / len(prices)
    sorted_p = sorted(prices)
    med_p = sorted_p[len(sorted_p) // 2]

    lines = [
        f"### Price History for {title}",
        f"- **Slug**: `{slug}`",
        f"- **Period**: Trailing {days} days",
        f"- **Total Observations**: {len(history)}",
        f"- **Min / Median / Mean / Max**: {currency} {min_p:,.2f} / {currency} {med_p:,.2f} / {currency} {mean_p:,.2f} / {currency} {max_p:,.2f}",
        "",
        "#### Recent Observations",
        "| Date | Price | Retailer | Source |",
        "| :--- | :--- | :--- | :--- |",
    ]

    for pt in history[-10:]:
        lines.append(f"| {pt.recorded_at[:10]} | {currency} {pt.price:,.2f} | {pt.retailer} | {pt.source} |")

    return "\n".join(lines)


@mcp.tool()
def watch_product(
    url_or_query: str,
    target_price: float | None = None,
    alert_on_deal: str = "great_deal",
) -> str:
    """
    Adds a product to the active watchlist. Resolves/fetches the product to seed history
    if not already cached, and sets alert trigger criteria.
    """
    init_db()
    try:
        meta, _, history = fetch_and_parse(url_or_query)
        upsert_product(meta)
        if history:
            save_price_points_batch(meta.product_slug, history)
        slug = meta.product_slug
        title = meta.title
    except Exception as e:
        _log(f"Could not scrape during watch_product, falling back to slug: {e}")
        slug = extract_listing_slug(url_or_query)
        title = slug

    item = WatchlistItem(
        product_slug=slug,
        url_or_query=url_or_query,
        target_price=float(target_price) if target_price is not None else None,
        alert_on_deal=alert_on_deal,
        created_at=datetime.utcnow().isoformat(),
        is_active=True,
    )
    upsert_watch(item)

    alert_desc = f"target price ≤ ₹{target_price:,.2f}" if target_price else f"deal rating meets '{alert_on_deal}'"
    return f"✅ **Watching `{slug}`** ({title})\n- Alert criteria: {alert_desc}\n- Check alerts periodically via `check_watchlist_alerts()`."


@mcp.tool()
def unwatch_product(product_slug: str) -> str:
    """Removes a product from the active watchlist by its product slug."""
    init_db()
    slug = extract_listing_slug(product_slug)
    removed = remove_watch(slug)
    if removed:
        return f"✅ Removed `{slug}` from active watchlist."
    return f"ℹ️ Product `{slug}` was not found on the active watchlist."


@mcp.tool()
def list_watchlist() -> str:
    """Lists all active watched products with their target criteria and latest known prices."""
    init_db()
    watches = list_watches(active_only=True)
    if not watches:
        return "ℹ️ Your price fair-value watchlist is currently empty. Use `watch_product` to track items."

    lines = [
        "### 📋 Active Price Watchlist",
        "| Product Slug | Title | Target Price | Alert On | Latest Price | Last Notified |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for w in watches:
        meta = get_product(w.product_slug)
        title = meta.title if meta else w.product_slug
        currency = meta.currency if meta else "INR"
        history = db_get_price_history(w.product_slug, days=3650)
        latest_price_str = f"{currency} {history[-1].price:,.2f}" if history else "Unknown"
        target_str = f"{currency} {w.target_price:,.2f}" if w.target_price else "Any"
        notified_str = w.last_notified_at[:10] if w.last_notified_at else "Never"

        lines.append(f"| `{w.product_slug}` | {title[:40]} | {target_str} | `{w.alert_on_deal}` | {latest_price_str} | {notified_str} |")

    return "\n".join(lines)


@mcp.tool()
def check_watchlist_alerts() -> str:
    """
    Checks all active watched items against current prices and verdict ratings.
    Refreshes pricing data, triggers alerts if criteria are met, and marks items notified.
    """
    init_db()
    watches = list_watches(active_only=True)
    if not watches:
        return "ℹ️ Watchlist is empty. No items to check."

    triggered: list[str] = []
    checked_count = 0

    for w in watches:
        checked_count += 1
        history: list[PricePoint] = []
        current_price = 0.0
        meta = get_product(w.product_slug)

        # Attempt fresh scrape
        try:
            fresh_meta, scraped_price, fresh_history = fetch_and_parse(w.url_or_query)
            upsert_product(fresh_meta)
            if fresh_history:
                save_price_points_batch(w.product_slug, fresh_history)
            history = fresh_history
            current_price = scraped_price
            meta = fresh_meta
        except Exception as e:
            _log(f"Could not refresh price online for {w.product_slug}: {e}")
            # Fallback to local DB history
            history = db_get_price_history(w.product_slug, days=180)
            if history:
                current_price = history[-1].price

        if not history or current_price <= 0:
            continue

        verdict = compute_fair_value_verdict(
            current_price=current_price,
            history=history,
            mrp=meta.mrp if meta else None,
            window_days=180,
        )

        # Evaluate trigger conditions
        is_triggered = False
        reasons: list[str] = []

        if w.target_price is not None and current_price <= w.target_price:
            is_triggered = True
            reasons.append(f"Price (₹{current_price:,.2f}) reached target (₹{w.target_price:,.2f})")

        if w.alert_on_deal == "great_deal" and verdict.deal_rating == "great_deal":
            is_triggered = True
            reasons.append(f"Rating achieved `{verdict.deal_rating}` ({verdict.verdict})")
        elif w.alert_on_deal == "good_deal" and verdict.deal_rating in ("great_deal", "good_deal"):
            is_triggered = True
            reasons.append(f"Rating achieved `{verdict.deal_rating}` ({verdict.verdict})")

        if is_triggered:
            now_iso = datetime.utcnow().isoformat()
            mark_watch_notified(w.product_slug, now_iso)
            title = meta.title if meta else w.product_slug
            triggered.append(
                f"- 🔔 **{title}** (`{w.product_slug}`): **₹{current_price:,.2f}**\n"
                f"  - Reasons: {', '.join(reasons)}\n"
                f"  - Verdict: {verdict.verdict} ({verdict.percentile}% percentile, median: ₹{verdict.median_price:,.2f})"
            )

    if not triggered:
        return f"✅ Checked {checked_count} watched item(s). No price drops or target criteria met at this time."

    return f"### 🚨 Price Alerts Triggered ({len(triggered)} item(s))\n" + "\n".join(triggered)


@mcp.tool()
def record_custom_observation(
    product_slug: str,
    price: float,
    currency: str = "INR",
    retailer: str = "manual",
) -> str:
    """Manually records a single price observation into the database."""
    init_db()
    slug = extract_listing_slug(product_slug)
    now_iso = datetime.utcnow().isoformat()

    # Ensure product exists
    existing = get_product(slug)
    if not existing:
        upsert_product(
            ProductMetadata(
                product_slug=slug,
                canonical_url="",
                title=slug,
                retailer=retailer,
                currency=currency,
                last_checked=now_iso,
            )
        )

    pt = PricePoint(
        price=float(price),
        recorded_at=now_iso,
        currency=currency,
        retailer=retailer,
        source="manual",
    )
    save_price_points_batch(slug, [pt])

    return f"✅ Recorded observation for `{slug}`: {currency} {price:,.2f} (Retailer: {retailer})"


@mcp.tool()
def evaluate_custom_series(
    product_name: str,
    current_price: float,
    history_prices: list[float],
    currency: str = "USD",
    mrp: float | None = None,
) -> str:
    """
    Evaluates a raw list of historical prices passed directly by the caller/LLM
    (for international sites like Amazon US, BestBuy, Keepa, CamelCamelCamel)
    without web scraping.
    """
    if not history_prices:
        return "❌ Error: `history_prices` list must contain at least one historical price."

    now = datetime.utcnow()
    # Create chronological points from the given prices
    history = [
        PricePoint(
            price=float(p),
            recorded_at=(now - timedelta(days=len(history_prices) - 1 - i)).isoformat(),
            currency=currency,
            retailer="custom",
            source="custom_series",
        )
        for i, p in enumerate(history_prices)
    ]

    verdict = compute_fair_value_verdict(
        current_price=float(current_price),
        history=history,
        mrp=float(mrp) if mrp is not None else None,
        window_days=3650,
    )

    lines = [
        f"### {verdict.verdict}",
        f"**Product**: {product_name}",
        f"- **Current Price**: {currency} {verdict.current_price:,.2f}",
    ]
    if mrp:
        lines.append(f"- **Claimed MRP**: {currency} {mrp:,.2f}")

    lines.extend([
        "",
        "#### Statistical Summary",
        f"- **Deal Rating**: `{verdict.deal_rating}`",
        f"- **Historical Percentile**: {verdict.percentile}% (cheaper than {verdict.percentile}% of historical points)",
        f"- **True Discount (vs Median)**: {verdict.true_discount_pct}%",
        f"- **Min / Median / Max**: {currency} {verdict.min_price:,.2f} / {currency} {verdict.median_price:,.2f} / {currency} {verdict.max_price:,.2f}",
        f"- **Confidence**: `{verdict.confidence}` ({verdict.sample_size} observations over {verdict.observation_span_days:.0f} days)",
    ])

    if verdict.is_deceptive_mrp:
        lines.extend([
            "",
            "⚠️ **DECEPTIVE MRP WARNING**: The claimed discount is inflated! "
            f"Seller claims **{verdict.fake_mrp_discount_pct}%**, but true discount against typical price is only **{verdict.true_discount_pct}%**.",
        ])

    return "\n".join(lines)


def main() -> None:
    """Main entrypoint for the FastMCP stdio server."""
    init_db()
    _log("Starting Price Fair-Value MCP server on stdio...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
