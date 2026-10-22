# INDstocks paper data connection

## Continuous signal observation

`python -m backend.indstocks_monitor` polls completed historical candles once per
minute during weekdays 09:15–15:35 IST. This is a signal observer, not an option
execution simulator. It saves current-day accepted/rejected setups idempotently
in `backend/data/indstocks_ema9_observations.sqlite` and health in
`backend/data/indstocks_ema9_status.json`. Earlier setups are labelled historical
observations and are never entered retroactively. All fills remain disabled.

The monitor currently requires a same-day BANKNIFTY instrument snapshot at
`backend/data/indstocks_fno.json`; it fails closed if the snapshot is stale.
Refresh the token and instrument snapshot as needed before subsequent sessions.
It does not install a scheduled task or restart automatically after a reboot.
Console output goes to the caller; when launched as a background process for this
session it is redirected to `indstocks_monitor.log` and `indstocks_monitor_error.log`.
Stop that monitor process to stop polling; the live trading server is independent.

This connector only fetches data. It has no order methods and is registered only
in the paper backend, not the dedicated live terminal. It does not start the
retired strategies or place broker orders.

## Setup

1. Generate an access token at https://www.indstocks.com/app/api-trading/access-tokens.
2. Add `INDSTOCKS_ACCESS_TOKEN=your_token` to the root `.env` locally. Do not paste
   the token into chat or commit it. No MPIN or TOTP secret is needed by this connector.
3. Restart only the paper backend. Open http://127.0.0.1:8003/docs and use the
   **INDstocks paper data** section.
4. `GET /api/paper-data/indstocks/status` checks configuration without a network request.
5. `POST /api/paper-data/indstocks/check` verifies authentication. This does not
   verify that current quotes are available or fresh.
6. `GET /api/paper-data/indstocks/quotes?codes=NFO_<current-token>` fetches full
   quotes. Obtain actual instrument tokens from INDstocks; Kotak tokens are not assumed interchangeable.

`POST /api/paper-data/indstocks/history` accepts `identifiers`, `start_ms`,
`end_ms`, `interval` (1minute or 5minute), and `expired` (default false).
Request at most seven days per call. Active instruments use SEGMENT_TOKEN codes;
expired NSE contracts use exact trading symbols. Missing symbols are reported.
Candle timestamps are opening times in epoch seconds; request times are milliseconds.
Consumers must exclude unfinished bars and map five-minute context only after close.

The Python client's `stream()` subscribes to quote-mode WebSocket messages with
SEGMENT:TOKEN identifiers. It yields raw events with local receipt times and raises
on disconnect. Consumers must explicitly handle reconnects, gaps and warmup; no
automatic strategy trading or stream recorder is enabled yet.

Quotes and streaming events have `timestamp_verified=false`. Do not use local
receipt time as exchange quote time or feed these unverified events into simulated
fills. Validate bid/ask fields and timestamp semantics against real payloads first.
HTTP errors are sanitized, redirects disabled, and REST calls serialized at no
more than four requests per second per process. Run one connector process.

Sources: [authentication](https://api-docs.indstocks.com/Users/),
[quotes](https://api-docs.indstocks.com/MarketQuote/),
[history](https://api-docs.indstocks.com/historicalData/),
[streaming](https://api-docs.indstocks.com/Websockets/).
