# Swing stock paper portfolio

**Current implementation:** [expectancy-based strategies, trailing exits and portfolio risk controls](./SWING_EXPECTANCY.md). The selective mandate and original baseline below are historical documentation, retained for reproducibility.

The active service now follows the [selective swing mandate](./STOCK_SELECTIVE_STRATEGY.md): no monthly goal, 20–25% gross targets, maximum 5% planned stop distance, and no new entries without validated probability above 80%. No model is currently validated. Strategy rules below describe the original baseline retained for historical comparisons and any existing filled positions.

The stock screener now runs a separate, broker-free paper account starting at
₹100,000. Open `/#stock-paper` on the frontend, or follow the link on the login
screen. This route does not mount broker authentication or market subscriptions.
The index swing and other terminal features are separate.

For a server that does not even start the terminal's unrelated broker services:

```powershell
.venv/Scripts/python.exe -m uvicorn backend.stock_paper_app:app --host 127.0.0.1 --port 8001
```

Use this **instead of** the full terminal backend on port 8001. The stock-only
server exposes no authentication or broker-order endpoints.

## Workflow

1. **Scan & update paper portfolio** downloads real Nifty 500 daily history and
   Nifty 50 benchmark bars through Yahoo/yfinance. Constituent source (current
   official file or bundled fallback) is shown in each saved scan.
2. All scanned stocks receive a retained decision: rejected, watch, qualified,
   or queued. No old research signals become paper trades.
3. Up to six ranked, confirmed daily breakouts are queued. A queued setup is
   valid only for the next observed benchmark session after its creation date.
   It fills at that session's open if eligible, otherwise it is skipped.
4. A scan first advances existing orders and holdings over completed sessions,
   then selects new setups. The scan cannot use earlier prices to fill a newly
   created idea. Repeated scans do not duplicate the same symbol/session setup.
5. After the first successful manual scan, the backend checks once per minute
   for a daily update after 16:10 IST on weekdays. The server must be running.
   Manual scans can retry failures and catch up sessions after downtime.

## Experimental selection rules

- Nifty 50 above its 50-day average; otherwise hold cash for new selections.
- Stock above ₹50, average 20-session turnover ≥₹10 crore.
- Close > 50-day SMA > 200-day SMA.
- Completed close above the prior 20-session high, volume ≥1.3× its previous
  20-session average, close in the upper 30% of the session range, and positive
  relative 20-session performance against Nifty 50.
- Stop below the five-session low with a 0.1 ATR buffer. Reject stop distances
  outside 2–8% and breakouts extended more than one ATR above the old high.
- Ranking combines relative strength, relative volume, and closing strength.
  This is not a calibrated probability or an independently validated edge.
- Planned target is 3R from the reference close, explicitly a planning rule,
  not a forecast. Require at least 2R remaining at the actual simulated fill.
- Entry open no more than 1% below reference; entry including slippage no more
  than half the initial risk above reference. Skip unacceptable gaps.

## Portfolio and accounting

- Long-only, unleveraged shares; no Kotak, F&O or order-placement calls.
- Six **filled entries** per Monday–Sunday week; fewer or zero is acceptable.
- Four simultaneous positions, two per sector, 25% equity allocation per stock.
- Per-entry loss budget is the lower of ₹750 and 0.75% equity, including assumed
  entry/stop execution costs. Actual gaps can exceed this planned risk.
- Model 0.05% adverse slippage and 0.10% costs on each side. These are simulation
  assumptions, not a claimed tax/broker fee schedule.
- Entries at the session open precede same-session exits; proceeds from later
  exits cannot fund earlier entries. Stop takes precedence when the same daily
  bar touches both stop and target; opening stop gaps fill at the worse open.
- Exit the entire position at stop, target, or the tenth held session's close.
- Splits adjust held quantity and levels; fractional paper entitlements can
  remain. Dividends are credited on ex-date as a simplified simulation, rather
  than modeling the later payment date. Pending setups on a split day are skipped.
- Missing active-symbol bars pause the whole account's session replay. Fresh
  benchmark data is required; no mock data or inferred fills are substituted.
- Open P&L uses the latest processed daily close and includes entry costs and
  credited dividends, but excludes future exit costs. Closed P&L includes both
  sides. Monthly return uses equity versus the last prior-month recorded equity
  (or starting capital for the first month). The 5% monthly goal does not alter
  sizing or force entries.

## Persistence and API

[stock_paper.py](../screener/stock_paper.py) owns the versioned SQLite account in
`screener/training_data/stock_paper_v1.sqlite3`. Transactions serialize account
updates. [stock_paper_service.py](../backend/stock_paper_service.py) owns the
daily data worker. Failed downloads leave the previous account intact.

- `GET /api/screener/paper`: account, queued orders, trades, equity and journal.
- `POST /api/screener/paper/scan`: start one asynchronous scan (202 response).
- `GET /api/screener/paper/scans`: saved scan list.
- `GET /api/screener/paper/scans/{id}`: every stock decision in that scan.
- `GET /api/screener/paper/export`: account plus all saved scans as JSON.

Legacy scanner endpoints return HTTP 410. Legacy research modules and databases
are inactive historical artifacts; their signals never feed this account.
Back up the new SQLite database with the backend stopped. There is no reset
button that silently destroys paper history.

## Validation

`python -m pytest tests/test_stock_paper.py -q`

The regression fixtures are synthetic **test data only**. They verify execution
timing, limits, costs, capital conservation, gaps, missing-data handling,
corporate actions, persistence and duplicate suppression; they do not establish
profitability. Production requires real provider data.

Provider documentation: [yfinance](https://ranaroussi.github.io/yfinance/).
Return expectations: [Investor.gov risk and return](https://www.investor.gov/additional-resources/information/youth/teachers-classroom-resources/risk-and-return).

## Historical one- and two-year tests

```powershell
.venv/Scripts/python.exe -m screener.stock_paper_backtest
```

The runner downloads 1,200 calendar days for warmup and testing, then calls the
same portfolio scan logic separately for each historical session. Every window
starts with ₹100,000 in an isolated temporary database; the running paper account
is untouched. Append `--refresh` to redownload instead of reusing saved real data.

Outputs are saved under `screener/index_research_cache/stock_paper_backtest/`:
`report.md` contains comparison and monthly tables; `results.json` contains
trades, skipped orders, daily equity and assumptions; `source_history.json.gz`
preserves the downloaded data for reproducibility. Source and strategy hashes
are recorded in the results.

Current constituents and sectors are applied historically, so the result has
survivorship/membership bias. Yahoo split-adjusted prices are converted back to
the units applicable on each simulated date before the production accounting
applies splits to positions. Only observations through the decision date enter
the signals. Missing active-symbol data aborts the run, rather than inventing
fills. First and last calendar months are labelled partial and excluded from
the count of months reaching the 5% goal. No parameter optimization is performed.
