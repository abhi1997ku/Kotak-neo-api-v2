# Intraday stock research

Paper UI: http://localhost:5174/?workspace=paper#intraday-stocks

This is an experimental paper simulation module. It never sends live orders.
No performance claim has been established.

## Implemented

- Top three per direction, with no forced selection.
- Same-clock cumulative relative volume against 20 prior complete sessions.
- Benchmark-relative performance, turnover and directional VWAP checks.
- Immutable 09:45 and 11:00 IST snapshots in SQLite.
- Late snapshots explicitly identified as historical reconstructions.
- Existing causal one-minute order-block rejection engine reused for signals.
- Signal journal; missing/stale data blocks evaluations.

## Data setup

The initial mapping covers 494 verified NSE cash instruments and Nifty 500.
Membership uses a labelled bundled fallback if the official download fails.
Do not guess tokens or substitute futures IDs for cash-stock IDs.

Create `backend/data/intraday_stocks/universe.json` from the provider's verified
instrument master, with `as_of` (today's ISO date), `benchmark` (provider code),
and `stocks` (object mapping stock symbols to provider cash instrument codes).
The benchmark must match the intended relative-strength comparison.

`python -m backend.intraday_stock_cache` loads up to 45 calendar days initially,
then incremental one-minute history through the existing read-only connector.
It requires an existing valid INDstocks data token. No token is exposed in UI.
Cache writes are atomic. The UI's Evaluate button only reads local candles.

## Automatic paper worker

Start/stop with the paper workspace launcher, or run
`python -m backend.intraday_paper_worker`. The worker uses a singleton SQLite
lock and persists account and health JSON atomically. It must remain running
with a valid data token. It refreshes instrument masters daily, freezes the
09:45 and 11:00 selections, and monitors selected/open/pending symbols.
Late startup yields historical reconstructions, never retroactive paper trades.

Entries activate on the first full minute after observation and expire three
minutes after the confirmation. Defaults: Rs 100,000 equity, 0.25% trade risk,
0.5% open risk, two positions, three entries/day, one entry per symbol/day,
90% gross exposure, 45% position cap, no leverage, 1% daily loss cutoff.
Both long and short notional count against capital. Costs use a conservative
0.1% each-side estimate and 0.05% adverse slippage; these are research
assumptions, not a verified broker tariff. Quantity is limited to 1% of prior
median minute volume. Exits use protective stops, a fixed 2R risk-based target,
and the 15:15 opening price. Stop takes precedence on ambiguous candles.
Missing position minutes pause execution rather than inventing prices. If
the process/feed stops, exits may be recorded later during candle catch-up.

Portfolio risk is not a guaranteed maximum loss: adverse gaps can exceed it.
Sector/correlation controls and chronological strategy-comparison backtests
remain research extensions; the worker does not claim validated profitability.

Existing BANKNIFTY and swing paper modules are unchanged.
