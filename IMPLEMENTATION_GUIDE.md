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
| `backend/data/nifty50_constituents.csv` | Local fallback for the Nifty 50 constituent list |
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

## Session and market data behavior

- The authenticated Kotak client and session tokens live in backend process memory. Restarting the backend clears the session; sign in again from the UI.
- Live quotes and option-chain prices need a valid broker session and instrument tokens. A watchlist symbol can be displayed even when its token or live quote is unavailable.
- The app attempts to use Kotak instrument masters and bundled local CSV files where available. If Kotak's instrument-master service is unavailable, some symbols or exchanges may not have tokens for live pricing.
- The screener supports synthetic mode for a sample scan and real mode using Yahoo Finance data.
- The order placement route currently rejects AMO requests with HTTP 503. Normal orders are handled through the Kotak Trade API.

## Common configuration issues

- **Unauthorized or expired session:** Sign in again with a current TOTP and MPIN. Confirm the root `.env` has the correct consumer key, registered mobile number, and UCC.
- **Kotak IP authorization:** The broker checks the public egress address of the backend host. Ensure that address is registered on the active Kotak API application; use a static egress IP where required.
- **No live prices:** Check backend logs and broker connectivity. Confirm the instrument-master source supplied a token for each requested instrument.
- **Frontend cannot reach the backend:** Confirm the backend is listening on port 8001 and the UI is opened on the same computer at localhost.

See the per-endpoint references in [docs/](docs/) for SDK request details.
