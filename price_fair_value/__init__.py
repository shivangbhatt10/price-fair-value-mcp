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
from price_fair_value.db import (
    get_db_path,
    init_db,
    upsert_product,
    save_price_points_batch,
    get_price_history,
    get_product,
    list_products,
    upsert_watch,
    remove_watch,
    list_watches,
    mark_watch_notified,
)

__all__ = [
    "PricePoint",
    "ProductMetadata",
    "VerdictResult",
    "WatchlistItem",
    "compute_fair_value_verdict",
    "filter_outliers_iqr",
    "get_db_path",
    "init_db",
    "upsert_product",
    "save_price_points_batch",
    "get_price_history",
    "get_product",
    "list_products",
    "upsert_watch",
    "remove_watch",
    "list_watches",
    "mark_watch_notified",
]
