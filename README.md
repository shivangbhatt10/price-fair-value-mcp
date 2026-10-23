# Price Fair-Value Alert MCP

An intelligent Model Context Protocol (MCP) server that tracks e-commerce product pricing, computes statistical fair-value verdicts (detecting fake discounts and marketplace spikes), and manages active price-drop watchlists.

## Project Status

- [x] **Milestone 1**: Isolated Environment Setup, Domain Models & Statistical Engine
- [x] **Milestone 2**: Persistent SQLite Storage & Watchlist Persistence Layer
- [ ] **Milestone 3**: Scraper Pipeline, MCP Server Tools & Automated Alert Engine

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

## Architecture (Milestone 1)

- `price_fair_value/models.py`: Domain data structures (`PricePoint`, `ProductMetadata`, `VerdictResult`, `WatchlistItem`).
- `price_fair_value/engine.py`: Statistical fair-value engine with IQR outlier filtering and mid-rank empirical CDF percentile deal rating.
