# Price Fair-Value Alert MCP

An intelligent Model Context Protocol (MCP) server that tracks e-commerce product pricing, computes statistical fair-value verdicts (detecting fake discounts and marketplace spikes), and manages active price-drop watchlists.

## Project Status

- [x] **Milestone 1**: Isolated Environment Setup, Domain Models & Statistical Engine
- [x] **Milestone 2**: Persistent SQLite Storage & Watchlist Persistence Layer
- [x] **Milestone 3**: PriceHistory.app Provider (URL Resolver, XOR Decryption, Meta Fallback)
- [x] **Milestone 4**: FastMCP Server Tools, OpenClaw Skill File & Full Test Suite

## Installation & Setup

1. **Virtual Environment**:
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Run Tests**:
   ```bash
   .\.venv\Scripts\python.exe -m pytest tests/ -v
   ```

3. **Run MCP Server (stdio)**:
   ```bash
   .\.venv\Scripts\python.exe server.py
   # or
   .\.venv\Scripts\python.exe -m price_fair_value.server
   ```

## MCP Tools Reference

- `evaluate_price`: Evaluates live product pricing from URL, computes percentile & true discount, warns on deceptive MRPs.
- `get_price_history`: Returns stored price points and historical summary from SQLite DB.
- `watch_product`: Adds product to watchlist with target price or deal rating criteria.
- `unwatch_product`: Removes product from watchlist.
- `list_watchlist`: Formatted table of actively monitored products.
- `check_watchlist_alerts`: Refreshes watched items and alerts on price drops.
- `record_custom_observation`: Manually logs price points into SQLite DB.
- `evaluate_custom_series`: Evaluates arbitrary raw price lists (Amazon US, Keepa, CamelCamelCamel).

## Architecture (Milestone 1)

- `price_fair_value/models.py`: Domain data structures (`PricePoint`, `ProductMetadata`, `VerdictResult`, `WatchlistItem`).
- `price_fair_value/engine.py`: Statistical fair-value engine with IQR outlier filtering and mid-rank empirical CDF percentile deal rating.
