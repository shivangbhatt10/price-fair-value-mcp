from __future__ import annotations

from datetime import datetime, timedelta, timezone
import statistics
from typing import Sequence

from price_fair_value.models import PricePoint, VerdictResult


def _parse_timestamp(recorded_at: str | datetime) -> datetime:
    """Robustly parse timestamps into naive UTC datetimes."""
    if isinstance(recorded_at, datetime):
        if recorded_at.tzinfo is not None:
            return recorded_at.astimezone(timezone.utc).replace(tzinfo=None)
        return recorded_at

    s = str(recorded_at).strip()
    cleaned = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    try:
        val = float(s)
        if val > 1e11:  # milliseconds
            val /= 1000.0
        return datetime.utcfromtimestamp(val)
    except Exception:
        pass

    return datetime.utcnow()


def _percentile(sorted_data: Sequence[float], p: float) -> float:
    """Compute empirical percentile using linear interpolation between closest ranks."""
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    if n == 1:
        return sorted_data[0]
    idx = (n - 1) * p
    low = int(idx)
    high = min(low + 1, n - 1)
    weight = idx - low
    return sorted_data[low] * (1.0 - weight) + sorted_data[high] * weight


def filter_outliers_iqr(prices: list[float]) -> list[float]:
    """
    If len(prices) >= 10, compute Q1 (25th percentile) and Q3 (75th percentile).
    Keep prices within [Q1 - 2.5 * IQR, Q3 + 2.5 * IQR] (if IQR == 0, keep prices within [0.3 * median, 2.5 * median])
    so extreme marketplace seller spikes (like 83,999 on a 28,000 item) don't skew the mean/max.
    """
    if len(prices) < 10:
        return list(prices)

    sorted_p = sorted(prices)
    q1 = _percentile(sorted_p, 0.25)
    q3 = _percentile(sorted_p, 0.75)
    iqr = q3 - q1

    if iqr == 0:
        med = float(statistics.median(sorted_p))
        lower = 0.3 * med
        upper = 2.5 * med
    else:
        lower = q1 - 2.5 * iqr
        upper = q3 + 2.5 * iqr

    filtered = [p for p in prices if lower <= p <= upper]
    return filtered if filtered else list(prices)


def compute_fair_value_verdict(
    current_price: float,
    history: list[PricePoint],
    mrp: float | None = None,
    window_days: int = 180,
) -> VerdictResult:
    """
    Compute statistical fair value verdict for current_price given historical PricePoints.
    """
    current_price = round(float(current_price), 2)

    if not history:
        fake_mrp_pct = (
            round((mrp - current_price) / mrp * 100, 1)
            if (mrp is not None and mrp > 0)
            else None
        )
        return VerdictResult(
            verdict="⚪ Insufficient history",
            deal_rating="insufficient_data",
            percentile=None,
            current_price=current_price,
            median_price=current_price,
            mean_price=current_price,
            min_price=current_price,
            max_price=current_price,
            true_discount_pct=None,
            fake_mrp_discount_pct=fake_mrp_pct,
            is_deceptive_mrp=False,
            confidence="low",
            sample_size=0,
            observation_span_days=0.0,
        )

    parsed_history: list[tuple[PricePoint, datetime]] = [
        (pt, _parse_timestamp(pt.recorded_at)) for pt in history
    ]

    latest_dt = max(dt for _, dt in parsed_history)
    cutoff_dt = latest_dt - timedelta(days=window_days)

    window_points = [pt for pt, dt in parsed_history if dt >= cutoff_dt]
    if len(window_points) < 5:
        selected_points = [pt for pt, _ in parsed_history]
    else:
        selected_points = window_points

    selected_dts = [_parse_timestamp(pt.recorded_at) for pt in selected_points]
    earliest_dt = min(selected_dts)
    latest_sel_dt = max(selected_dts)
    observation_span_days = round(
        max(0.0, (latest_sel_dt - earliest_dt).total_seconds() / 86400.0), 1
    )

    raw_prices = [
        float(pt.price) for pt in selected_points if pt.price is not None and pt.price > 0
    ]
    prices = filter_outliers_iqr(raw_prices)
    n = len(prices)

    fake_mrp_discount_pct = (
        round((mrp - current_price) / mrp * 100, 1)
        if (mrp is not None and mrp > 0)
        else None
    )

    if n < 2:
        single_p = prices[0] if prices else current_price
        return VerdictResult(
            verdict="⚪ Insufficient history",
            deal_rating="insufficient_data",
            percentile=None,
            current_price=current_price,
            median_price=single_p,
            mean_price=single_p,
            min_price=single_p,
            max_price=single_p,
            true_discount_pct=None,
            fake_mrp_discount_pct=fake_mrp_discount_pct,
            is_deceptive_mrp=False,
            confidence="low",
            sample_size=n,
            observation_span_days=observation_span_days,
        )

    median_price = round(float(statistics.median(prices)), 2)
    mean_price = round(float(statistics.mean(prices)), 2)
    min_price = round(float(min(prices)), 2)
    max_price = round(float(max(prices)), 2)

    pricier = sum(1 for p in prices if p > current_price + 0.005)
    ties = sum(1 for p in prices if abs(p - current_price) <= 0.005)
    percentile = round((pricier + 0.5 * ties) / n * 100, 1)

    is_near_min = (current_price <= min_price * 1.03) and (max_price - min_price > 0.01)
    if percentile >= 80 or is_near_min:
        deal_rating = "great_deal"
        verdict = "🟢 Great deal — near historical low"
    elif percentile >= 60:
        deal_rating = "good_deal"
        verdict = "🟢 Good deal — meaningfully below typical price"
    elif percentile >= 40:
        deal_rating = "fair_price"
        verdict = "🟡 Fair — roughly the going rate"
    elif percentile >= 20:
        deal_rating = "not_great"
        verdict = "🟠 Not great — above typical price"
    else:
        deal_rating = "overpriced"
        verdict = "🔴 Bad deal — near historical high"

    if median_price > 0:
        true_discount_pct = round((median_price - current_price) / median_price * 100, 1)
    else:
        true_discount_pct = 0.0

    if fake_mrp_discount_pct is not None:
        is_deceptive_mrp = (fake_mrp_discount_pct - true_discount_pct) >= 15.0
    else:
        is_deceptive_mrp = False

    if n < 5:
        confidence = "low"
    elif n >= 20 and observation_span_days >= 60:
        confidence = "high"
    else:
        confidence = "medium"

    return VerdictResult(
        verdict=verdict,
        deal_rating=deal_rating,
        percentile=percentile,
        current_price=current_price,
        median_price=median_price,
        mean_price=mean_price,
        min_price=min_price,
        max_price=max_price,
        true_discount_pct=true_discount_pct,
        fake_mrp_discount_pct=fake_mrp_discount_pct,
        is_deceptive_mrp=is_deceptive_mrp,
        confidence=confidence,
        sample_size=n,
        observation_span_days=observation_span_days,
    )
