from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PricePoint:
    price: float
    recorded_at: str
    currency: str = "INR"
    retailer: str = "amazon.in"
    source: str = "pricehistory.app"
    offer_price: float | None = None
    deal_type: str | None = None


@dataclass
class ProductMetadata:
    product_slug: str
    canonical_url: str
    title: str
    retailer: str
    currency: str = "INR"
    mrp: float | None = None
    last_checked: str | None = None


@dataclass
class VerdictResult:
    verdict: str
    deal_rating: str
    percentile: float | None
    current_price: float
    median_price: float
    mean_price: float
    min_price: float
    max_price: float
    true_discount_pct: float | None
    fake_mrp_discount_pct: float | None
    is_deceptive_mrp: bool
    confidence: str
    sample_size: int
    observation_span_days: float


@dataclass
class WatchlistItem:
    product_slug: str
    url_or_query: str
    target_price: float | None = None
    alert_on_deal: str = "great_deal"
    created_at: str = ""
    last_notified_at: str | None = None
    is_active: bool = True
