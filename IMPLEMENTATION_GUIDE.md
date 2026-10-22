# Implementation Guide

## Overview

This repository runs a local, single-account Kotak Neo trading dashboard with a Python FastAPI backend and a React/Vite frontend.

```text
Browser UI: http://localhost:5173
        |
        | HTTP and WebSocket
        v
FastAPI: http://127.0.0.1:8001
        |
        | Kotak Neo SDK and broker endpoints
        v
Kotak Neo
```

The frontend API client currently targets `http://localhost:8001/api`. Keep the backend on port 8001 unless you update that client and the CORS settings together.

## Configure and run

Follow [README.md](README.md) for prerequisites, root `.env` setup, dependency installation, and server startup. Use [SERVER_COMMANDS.md](SERVER_COMMANDS.md) to control the Windows background servers.

The application reads these settings from the repository-root `.env`:

| Name | Required | Purpose |
| --- | --- | --- |
| `KOTAK_CONSUMER_KEY` | Yes | Consumer key for the Kotak Trade API application |
| `KOTAK_MOBILE` | Yes | Registered mobile number, including country code |
| `KOTAK_UCC` | Yes | Kotak Neo unique client code |
| `KOTAK_ENVIRONMENT` | No | Kotak environment; defaults to `prod` |

Users enter the current TOTP and MPIN on the dashboard login form. The app does not need a stored TOTP code or MPIN in `.env`.

## Source layout

| Path | Responsibility |
| --- | --- |
| `frontend/src/components/` | Dashboard, watchlist, order, position, holding, option-chain, and screener UI |
| `frontend/src/contexts/AuthContext.tsx` | Frontend login and authorization state |
| `frontend/src/services/api.ts` | HTTP and WebSocket client; backend base URL is defined here |
| `backend/main.py` | FastAPI application, CORS configuration, and route registration |
| `backend/routes/` | Authentication, market data, orders, positions, watchlist, and screener endpoints |
| `backend/session_manager.py` | In-memory authenticated Kotak client for the single-user app |
| `backend/kotak_client.py` | Kotak SDK wrapper, quote/option data normalization, and instrument lookup |
| `backend/watchlist_catalog.py` | Index and Nifty 50 watchlist catalog |
| `backend/data/nifty50_constituents.csv`, `backend/data/nifty500_constituents.csv` | Local fallbacks for the selected NSE index universe |
| `neo_api_client/` | Kotak Neo SDK implementation and bundled instrument files |
| `screener/` | Swing-trade scan and result storage |

## Backend routes

The API prefix is `/api`. FastAPI's interactive route documentation is available at `http://localhost:8001/docs`.

| Area | Routes |
| --- | --- |
| Authentication | `POST /api/auth/login`, `GET /api/auth/status`, `POST /api/auth/logout` |
| Market data | `GET /api/market/quotes`, `GET /api/market/search`, `GET /api/market/option-chain` |
| Live market data | `/api/watchlist/stream` and `/api/market/option-chain/stream` WebSockets |
| Orders | `GET /api/orders/book`, `GET /api/orders/trade-book`, `POST /api/orders/place`, `POST /api/orders/modify`, `POST /api/orders/cancel` |
| Portfolio | `GET /api/positions/`, `GET /api/positions/holdings`, `GET /api/positions/margin` |
| Screener | `GET /api/screener/scan`, `GET /api/screener/latest` |
| Health | `GET /health` |

## Dashboard and order workflow

- The left sidebar contains margin data, instrument search, a dashboard refresh action, logout, and an expandable Holdings panel. Holdings are fetched from `GET /api/positions/holdings` and refresh every 10 seconds while the panel is open.
- Each holding has a **Sell holding** action. It opens the shared order ticket with Sell selected, the holding quantity prefilled, and that quantity enforced as the maximum. The holding sell ticket defaults to CNC and keeps the product fixed. A partial quantity can be entered.
- Kotak holding records may not include optional trading-symbol or exchange metadata in the app response. In that case, the ticket uses the holding symbol with the `-EQ` suffix and the NSE cash segment (`nse_cm`). It uses the holding's current price as the displayed LTP fallback.
- The shared order ticket is also used by watchlist/search and option-chain Buy/Sell actions, and by position exits. Position exits use the position's product and cap the quantity to the open position amount.
- On successful submission, the ticket closes and the app refreshes its order book, positions, holdings, and trade book data. Orders in the order book are displayed newest first using Kotak's order timestamp.
- Market and limit orders are available in the ticket. AMO placement through this terminal is currently unavailable; see [README.md](README.md#what-runs-in-this-repository).

See `Dashboard.tsx`, `HoldingsPanel.tsx`, `OrderTicketModal.tsx`, and `OrdersPanel.tsx` under `frontend/src/components/` for these UI flows.

## Margin data

The margin panel refreshes every five seconds through GET /api/positions/margin. The backend requests Kotak RMS limits with segment, exchange, and product set to ALL. Available Margin displays Kotak's Net value. Gross Margin Available uses a gross value if returned, then CollateralValue, then Net plus MarginUsed. Utilised displays MarginUsed, and P&L sums Kotak's realized and unrealized MTM values. Invalid or unavailable broker data is reported to the panel instead of being replaced with fabricated zero balances.

The sample field names and response shape are documented in the local Kotak reference at docs/Limits.md.
## Swing trade scanner

- The dashboard scanner is an on-demand research report at `GET /api/screener/scan`. Choose Nifty 50 or Nifty 500 in the scanner; current official membership is refreshed from NSE and bundled constituent CSVs provide a fallback. If `screener/watchlist.txt` exists, that custom list takes precedence. Historical daily OHLCV comes from Yahoo Finance and is downloaded in 50-symbol batches. Synthetic prices are never substituted; incomplete coverage is reported.
- The report groups at most 3 short swings (1–2 sessions), 5 weekly swings (3–10 sessions), and 5 monthly swings (2–8 weeks). It summarizes Nifty 50/500 and global benchmark trends, India VIX, breadth for the selected stock universe, and constituent-industry relative performance. Sector rotation is a constituent sample, not a full sector-index ranking.
- Kotak LTP requests are made only for the shortlisted symbols, using batches within Kotak's request limit; the scanner does not request live quotes for every Nifty 500 member. Missing instrument tokens or quotes mark that candidate `NO TRADE` and appear in coverage.
- Daily technical measures include 20/50/100/200-day averages, RSI, MACD, ADX, ATR, volume versus its 20-session average, support/resistance, and relative strength. Weekly averages and returns are derived by resampling daily bars. VWAP measures are daily-bar approximations; they are not intraday VWAP.
- Yahoo Finance aggregated financial metrics, a preliminary earnings/ex-dividend calendar check, and recent source-linked headlines are fetched best-effort for shortlisted symbols. Headline keyword matches are flags for manual review, not verified event classifications; confirm dates and announcements at the issuer/exchange source.
- After selecting candidates, the existing authenticated Kotak v2 client uses Kotak's current NSE F&O scrip master and quote API to fetch nearest stock futures plus up to seven strikes closest to spot for the nearest stock-option expiry. The displayed put/call OI ratio covers only that near-ATM slice, not the full chain. If Kotak's live F&O instrument master is down or quote/OI fields are missing, the scanner reports unavailable/partial and does not use the SDK's stale local derivative tokens. This does not modify order placement or replace the SDK.
- FII/DII cash-market flows and security-wise delivery positions require an authorized data source. The scanner does not scrape NSE's public website: NSE's terms prohibit systematic/automated collection. FII/DII values are aggregate daily flows; delivery values are end-of-day deliverable quantities and percentages, not intraday volume. The UI shows `provider required` until an authorized report is imported.
- **Import authorized reports:** Expand **Import authorized research reports** above Scan. Upload the FII/FPI and DII CSV downloaded through an authorized source, the NSE `MTO_ddmmyyyy.DAT` security-wise delivery file (or compatible CSV), and/or a corporate-events CSV with `symbol,event,date` and optional `url,source` columns. FII CSV headers must identify category, date, buy value, sell value, and net value. Delivery and event records are stored in the ignored local `screener_market_feeds.db`; repeated delivery imports build a five-session average. The corporate-events importer accepts event dates from 14 days ago through 30 days ahead; Yahoo headlines/calendar remain clearly marked preliminary. Import reports manually; the app makes no exchange-site requests.
- Scoring is visible out of 100: technical structure 25, price action 15, volume 10, relative strength 10, sector strength 10, fundamentals/catalyst 10, market regime 10, and risk/reward 10. Missing evidence scores zero. Scores rank rule-based setups; they are not probabilities or a promise of returns. C setups are marked AVOID. Cash-share sizing is shown for long ideas using the entered capital and risk settings (risk is capped at 1%); short exposure is not sized because derivatives do not provide verified account margin for the proposed position.
- Use the UI controls to set reference capital, maximum risk per trade (up to 1%), and minimum R:R. Entry, stop, three targets, maximum loss, and quantity are research estimates derived from daily OHLCV, ATR, and recent structure. Check the latest price and verify the entry condition before placing any separate order. No orders are submitted by the scanner.
- The Signal performance panel records new rule-based and AI-pattern candidates except `AVOID` and `NO TRADE` in `screener_results.db`. It watches each signal for 10 completed trading sessions (about two weeks), starts the paper entry only when a later daily bar reaches the entry zone, and records a target, stop, missed entry, or time exit. A same-day stop/target collision is counted as a stop because daily OHLCV does not reveal the intraday sequence. Results use the underlying price, not option premium, and omit fees/slippage. The panel refreshes on demand and every five minutes while open; previous signals are not backfilled. This is an observation log only and cannot submit orders.
- `GET /api/screener/signal-performance` refreshes active signal observations from Yahoo Finance daily bars and adds a Kotak LTP mark when the broker session is available. Win rate includes only entered, resolved signals; missed entries are excluded.
- For additional option-chain/OI fields beyond the current v2 quote payload, Kotak's current Python SDK exposes `option_chain(exchange="nse_fo", underlying=..., instrument_type="option")`. Review the [official v2-to-v3 migration guide](https://github.com/Kotak-Neo/kotak-neo-python/blob/main/docs/guides/MIGRATION.md) before any SDK upgrade. Do not query all 500 symbols: most are not F&O underlyings and chain/quote APIs are rate-limited. Upgrade in a separate environment first: v2 and v3 install the same `neo_api_client` import package, and the migration changes WebSocket and order behavior.
- Official references: NSE [FII/FPI and DII reports](https://www.nseindia.com/reports/fii-dii), [option chain](https://www.nseindia.com/option-chain), [security-wise delivery and EOD reports](https://www.nseindia.com/all-reports), [corporate filings and announcements](https://www.nseindia.com/companies-listing/corporate-filings-announcements), [corporate actions](https://www.nseindia.com/companies-listing/corporate-filings-actions), and [website terms](https://www.nseindia.com/static/nse-terms-of-use). NSE Data & Analytics publishes [real-time subscriptions](https://www.nseindia.com/static/market-data/real-time-data-subscription), [EOD/historical subscriptions](https://www.nseindia.com/static/market-data/eod-historical-data-subscription), and a [data usage policy](https://www.nseindia.com/static/market-data/nse-data-policy). Automated collection of data from the public NSE website is not enabled; use an authorized API/feed or manually provided report within its license terms.
- Scan timestamps use India time; each setup shows its latest daily bar date, Kotak price snapshot if available, source links, and data-coverage limitations. The scan is manual and runs when **Scan** is clicked; it does not create scheduled daily or weekend reports.

## Session and market data behavior

- The authenticated Kotak client and session tokens live in backend process memory. Restarting the backend clears the session; sign in again from the UI.
- Live quotes and option-chain prices need a valid broker session and instrument tokens. A watchlist symbol can be displayed even when its token or live quote is unavailable.
- The app attempts to use Kotak instrument masters and bundled local CSV files where available. If Kotak's instrument-master service is unavailable, some symbols or exchanges may not have tokens for live pricing.
- Synthetic sample data remains available only through local development helpers/CLI; the dashboard scan endpoint always uses real market data.
- The order placement route currently rejects AMO requests with HTTP 503. Normal orders are handled through the Kotak Trade API.

## Common configuration issues

- **Unauthorized or expired session:** Sign in again with a current TOTP and MPIN. Confirm the root `.env` has the correct consumer key, registered mobile number, and UCC.
- **Kotak IP authorization:** The broker checks the public egress address of the backend host. Ensure that address is registered on the active Kotak API application; use a static egress IP where required.
- **No live prices:** Check backend logs and broker connectivity. Confirm the instrument-master source supplied a token for each requested instrument.
- **Frontend cannot reach the backend:** Confirm the backend is listening on port 8001 and the UI is opened on the same computer at localhost.

See the per-endpoint references in [docs/](docs/) for SDK request details.
