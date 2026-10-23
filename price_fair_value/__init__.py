"""Price Fair-Value MCP package."""

from price_fair_value.models import (
    PricePoint,
    ProductMetadata,
    VerdictResult,
    WatchlistItem,
)
from price_fair_value.engine import (
    compute_fair_value_verdict,
    filter_outliers_iqr,
)

__all__ = [
    "PricePoint",
    "ProductMetadata",
    "VerdictResult",
    "WatchlistItem",
    "compute_fair_value_verdict",
    "filter_outliers_iqr",
]
