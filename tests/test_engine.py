from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from price_fair_value.models import PricePoint, VerdictResult
from price_fair_value.engine import (
    filter_outliers_iqr,
    compute_fair_value_verdict,
)


def _generate_history(
    prices: list[float],
    start_date: str = "2024-01-01T10:00:00",
    interval_days: int = 1,
) -> list[PricePoint]:
    """Helper to generate history with predictable timestamps."""
    base_dt = datetime.fromisoformat(start_date)
    return [
        PricePoint(
            price=p,
            recorded_at=(base_dt + timedelta(days=i * interval_days)).isoformat(),
        )
        for i, p in enumerate(prices)
    ]


class TestOutlierFiltering:
    def test_iqr_zero_spike_filtered(self):
        """When 14 prices are identical at 28,000 and 1 spike at 83,999 occurs."""
        prices = [28000.0] * 14 + [83999.0]
        filtered = filter_outliers_iqr(prices)
        assert 83999.0 not in filtered
        assert len(filtered) == 14
        assert all(p == 28000.0 for p in filtered)

    def test_iqr_nonzero_spike_filtered(self):
        """When prices have variance and an extreme marketplace spike is present."""
        prices = [
            27000.0,
            27500.0,
            28000.0,
            28200.0,
            28500.0,
            28000.0,
            27800.0,
            28100.0,
            28300.0,
            28000.0,
            83999.0,
        ]
        filtered = filter_outliers_iqr(prices)
        assert 83999.0 not in filtered
        assert max(filtered) <= 28500.0

    def test_short_price_list_bypasses_filter(self):
        """Fewer than 10 prices should not be filtered."""
        prices = [28000.0, 28000.0, 83999.0]
        filtered = filter_outliers_iqr(prices)
        assert filtered == prices


class TestVerdictCalculations:
    def test_flat_price_50th_percentile_fair_price(self):
        """Flat price history should yield 50th percentile and 'fair_price' verdict."""
        history = _generate_history([1000.0] * 12)
        res = compute_fair_value_verdict(current_price=1000.0, history=history)

        assert res.percentile == 50.0
        assert res.deal_rating == "fair_price"
        assert res.verdict == "🟡 Fair — roughly the going rate"
        assert res.median_price == 1000.0
        assert res.true_discount_pct == 0.0

    def test_great_deal_vs_bad_deal(self):
        """Test near-low (great deal) vs near-high (bad deal / overpriced)."""
        # Distribution: 1000, 1200, 1400, 1500, 1600, 1800, 2000
        history = _generate_history([1000.0, 1200.0, 1400.0, 1500.0, 1600.0, 1800.0, 2000.0])

        # Current price near historical low
        great_res = compute_fair_value_verdict(current_price=1020.0, history=history)
        assert great_res.deal_rating == "great_deal"
        assert great_res.verdict == "🟢 Great deal — near historical low"
        assert great_res.min_price == 1000.0
        assert great_res.current_price <= great_res.min_price * 1.03

        # Current price higher than historical maximum
        bad_res = compute_fair_value_verdict(current_price=2200.0, history=history)
        assert bad_res.deal_rating == "overpriced"
        assert bad_res.verdict == "🔴 Bad deal — near historical high"
        assert bad_res.percentile == 0.0

    def test_good_deal_and_not_great_tiers(self):
        """Test good_deal (>= 60) and not_great (>= 20, < 40)."""
        history = _generate_history([1000.0, 1200.0, 1400.0, 1500.0, 1600.0, 1800.0, 2000.0])

        # Current price at 1250: pricier = 5 (1400, 1500, 1600, 1800, 2000), ties = 0.
        # percentile = 5 / 7 * 100 = 71.4 -> good_deal
        good_res = compute_fair_value_verdict(current_price=1250.0, history=history)
        assert good_res.deal_rating == "good_deal"
        assert good_res.verdict == "🟢 Good deal — meaningfully below typical price"

        # Current price at 1750: pricier = 2 (1800, 2000), ties = 0.
        # percentile = 2 / 7 * 100 = 28.6 -> not_great
        not_great_res = compute_fair_value_verdict(current_price=1750.0, history=history)
        assert not_great_res.deal_rating == "not_great"
        assert not_great_res.verdict == "🟠 Not great — above typical price"

    def test_deceptive_mrp_detection(self):
        """Detect inflated MSRP trick when fake discount exceeds true discount by >= 15%."""
        # Median price is 25000.0
        history = _generate_history([25000.0] * 10)

        # MRP 35,000, current price 24,000:
        # true_discount = (25000 - 24000) / 25000 * 100 = 4.0%
        # fake_mrp_discount = (35000 - 24000) / 35000 * 100 = 31.4%
        # diff = 27.4% >= 15% -> deceptive
        deceptive_res = compute_fair_value_verdict(
            current_price=24000.0,
            history=history,
            mrp=35000.0,
        )
        assert deceptive_res.is_deceptive_mrp is True
        assert deceptive_res.true_discount_pct == 4.0
        assert deceptive_res.fake_mrp_discount_pct == 31.4

        # Genuine MRP of 26,000, current price 24,000:
        # fake_mrp_discount = (26000 - 24000) / 26000 * 100 = 7.7%
        # diff = 3.7% < 15% -> not deceptive
        honest_res = compute_fair_value_verdict(
            current_price=24000.0,
            history=history,
            mrp=26000.0,
        )
        assert honest_res.is_deceptive_mrp is False
        assert honest_res.fake_mrp_discount_pct == 7.7

        # No MRP provided
        none_mrp_res = compute_fair_value_verdict(
            current_price=24000.0,
            history=history,
            mrp=None,
        )
        assert none_mrp_res.is_deceptive_mrp is False
        assert none_mrp_res.fake_mrp_discount_pct is None

    def test_confidence_calculation(self):
        """Confidence is high if n >= 20 and span >= 60 days, low if n < 5, medium otherwise."""
        # High confidence: 25 points spanning 96 days (interval = 4 days)
        high_history = _generate_history([1000.0] * 25, interval_days=4)
        res_high = compute_fair_value_verdict(current_price=1000.0, history=high_history)
        assert res_high.sample_size >= 20
        assert res_high.observation_span_days >= 60.0
        assert res_high.confidence == "high"

        # Medium confidence case A: 25 points spanning only 12 days (interval = 0.5 days -> span < 60)
        med_history_short_span = _generate_history([1000.0] * 25, interval_days=1)
        res_med1 = compute_fair_value_verdict(current_price=1000.0, history=med_history_short_span)
        assert res_med1.observation_span_days < 60.0
        assert res_med1.confidence == "medium"

        # Medium confidence case B: 10 points spanning 90 days (n < 20, but >= 5)
        med_history_few_pts = _generate_history([1000.0] * 10, interval_days=9)
        res_med2 = compute_fair_value_verdict(current_price=1000.0, history=med_history_few_pts)
        assert 5 <= res_med2.sample_size < 20
        assert res_med2.confidence == "medium"

        # Low confidence: 4 points (< 5)
        low_history = _generate_history([1000.0] * 4, interval_days=10)
        res_low = compute_fair_value_verdict(current_price=1000.0, history=low_history)
        assert res_low.sample_size == 4
        assert res_low.confidence == "low"

        # Insufficient data: 1 point (< 2)
        single_pt_history = _generate_history([1000.0], interval_days=1)
        res_insufficient = compute_fair_value_verdict(current_price=1000.0, history=single_pt_history)
        assert res_insufficient.sample_size == 1
        assert res_insufficient.confidence == "low"
        assert res_insufficient.deal_rating == "insufficient_data"
        assert res_insufficient.verdict == "⚪ Insufficient history"

    def test_trailing_window_filtering(self):
        """Verify points older than window_days are filtered when window has >= 5 points."""
        # 10 old points from 250 days ago at 5000, 10 recent points within 30 days at 2000
        old_points = _generate_history([5000.0] * 10, start_date="2023-01-01T00:00:00", interval_days=5)
        recent_points = _generate_history([2000.0] * 10, start_date="2023-10-01T00:00:00", interval_days=5)
        all_history = old_points + recent_points

        res = compute_fair_value_verdict(current_price=2000.0, history=all_history, window_days=180)
        # Should only evaluate the 10 recent points at 2000.0
        assert res.sample_size == 10
        assert res.median_price == 2000.0
        assert res.max_price == 2000.0
