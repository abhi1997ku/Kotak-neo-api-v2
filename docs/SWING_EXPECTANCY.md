# Expectancy-based stock swing workspace

This supersedes the monthly-return goal, fixed-profit targets and unvalidated 80% probability gate. All activity is paper only. No Kotak authentication, live quotes, subscriptions or order endpoints are invoked. Existing Kotak integration remains available to unrelated terminal features; the standalone stock application deliberately does not start those services.

## Run and verify

From the repository root:

```powershell
.venv/Scripts/python.exe -m uvicorn backend.stock_paper_app:app --host 127.0.0.1 --port 8002
.venv/Scripts/python.exe -m screener.swing_data
.venv/Scripts/python.exe -m screener.swing_backtest
.venv/Scripts/python.exe -m pytest tests/test_swing_engine.py tests/test_stock_paper.py tests/test_stock_paper_backtest.py tests/test_stock_ai_research.py tests/test_stock_swing_experiments.py tests/test_stock_selective.py -q
```

Open the frontend at `/#stock-paper`. The separate route avoids mounting broker authentication. The daily loop runs after 16:10 IST on weekdays once a first scan enables tracking; it requires the backend to remain running. A manual scan catches up held positions over missed completed sessions, but does not fabricate new entries on missed scan dates.

The stock API uses port 8002 independently of the broader terminal on 8001. On first access, SQLite's consistent backup copies the legacy paper account and scans into `screener/training_data/stock_swing_v3.sqlite3`. The original database is not modified. Later operations use only the new database, so an older running terminal worker cannot change this portfolio.

## Data and universe

[Daily cache](../screener/swing_data.py) reuses the project's Yahoo downloader. It requests up to 1,200 calendar days for missing symbols and normally only ten overlapping calendar days for incremental updates. Per-symbol request timestamps limit duplicate refreshes, including unsuccessful symbols. Official Nifty 500 constituents are requested from NSE, then the alternative Nifty Indices file. A bundled/cached fallback is explicitly identified, never described as a successful current refresh.

The actual Nifty 500 series (`^CRSLDX`) supplies market regime and relative strength; no Nifty 50 substitution is allowed. At least 250 completed candles are required for the full indicator set. Missing active-position or queued-entry data pauses replay. A stale benchmark (>4 calendar days), missing benchmark, or incomplete benchmark candle prevents scanning.

Split-adjusted source prices are restored to each historical decision date's share units for execution. Positions adjust quantity and levels on split dates; dividends use a simplified ex-date cash credit. Incremental data with changed overlapping price bases or split records triggers a full symbol refresh; a failed full refresh quarantines that symbol. Other reorganizations, delistings and historical membership are not reconstructed. These limitations are reported, not silently approximated as cash returns.

## Independent entry strategies

All require price >=INR 50, turnover >=INR 10 crore, price above SMA50, rising SMA50, positive aligned 60-session relative strength versus Nifty 500, volume >=1.2 times the prior 20-session average, and a close in the upper 35% of the daily range.

- **Breakout 20 / 55:** close above the previous 20/55-session high, excluding today's candle; extension no more than one ATR. The bullish SMA50/SMA200 structure earns more score points, but is not mandatory for breakout research.
- **Pullback:** SMA50 above SMA200; previous low touches within 1% of EMA20, previous close not more than 2% below it; today's close clears the previous high and EMA20, within 1.5 ATR of the EMA. Falling price alone does not qualify.
- **Contraction:** confirmed 20-session breakout plus prior five-session ATR / prior 20-session ATR <0.8 and prior 10-session range / prior 30-session range <0.65; volume threshold increases to 1.4 times average. Contraction measurements exclude the breakout candle.

Market is bearish when below SMA200 with falling SMA50; bullish when close >SMA50 >SMA200, SMA50 rises, and annualized 20-session volatility is <35%; otherwise sideways. Bearish conditions block new entries, and sideways conditions require five additional score points. These are research definitions, not universal regime labels.

## Quality score and events

The maximum score is 100: trend 20, setup/close quality 20, relative strength 15, volume 10, market regime 10, liquidity 10, volatility/stop distance 5, potential reward/risk 5, and reliable event clearance 5. Each component is retained in the report. The default threshold is 65; mandatory setup/risk controls cannot be overridden by score.

No reliable event calendar is currently connected. Event risk is **UNKNOWN**, earns zero clearance points and is displayed. An optional member event record (`known_at`, `blocked_until`, `risk`) can block an entry only when the information was known on the simulated signal date. Historical events are never inferred from later announcements.

## Entries, stops and exits

Signals are generated after a completed close. Planned entry includes slippage; actual entry is at the next session open only if it remains above the confirmed trigger and no more than 0.5 ATR above the signal close. A gap outside the band is skipped. Intraday entries are not guessed from daily highs.

Initial stop is the lower of the five-session swing low minus 0.1 ATR and signal close minus the configured ATR multiple. Default permitted stop distance is 1.5–8%, rechecked at actual fill. Backtests compare ATR multiples 2 and 3; the default research setting is 3.

There is **no fixed profit target**. Room to the previous 250-session high is displayed as potential reward/risk, not used as a take-profit order. A missing overhead high is shown as unknown/uncapped, not an invented reward forecast. A 3R reference level is recorded for analysis only.

Exit alternatives:

1. **Chandelier:** highest high since entry minus the configured ATR multiple.
2. **Swing:** latest five-session low minus 0.1 ATR.
3. **EMA reversal:** two completed closes below EMA20 generate an exit signal for the next open; the initial protective stop remains active.

Stop ratchets cannot move down, except proportional corporate-action unit adjustments. New trailing levels are computed after current-session execution and effective next session. A computed level above the close is capped just below the close. Overnight gap stops fill at the worse open with adverse slippage. Stop/exit signals and executions have separate journal events. Existing filled legacy trades retain their original stop, target and holding-time rules rather than silently changing the bargain.

## Position sizing and portfolio constraints

Default equity INR 100,000; risk budget 0.5% of current equity per entry. Estimated risk includes the stop distance, 1% entry-price gap allowance, adverse stop slippage and entry/exit charges. Quantity is floored and reduced until risk, cash and every exposure cap are satisfied. The gap allowance is a sizing reserve, not a guarantee.

- Total open portfolio risk <=2% of marked equity for new entries. Existing risk is calculated from actual quantity, current mark and current stop plus exit costs/gap allowance; it is not the sum of initial risk budgets.
- Maximum four simultaneous positions and six filled entries per Monday–Sunday week.
- Capital <=25% per stock, <=35% per sector, and <=90% overall.
- Correlation uses up to 60 aligned daily returns with at least 40 observations. Exposure in stocks correlated >=0.8 to a proposed stock is capped at 35% equity. Unknown correlation is conservatively treated as correlated. This is a pairwise exposure control, not a full covariance/VaR model.
- Order size <=0.1% of average traded shares and average daily turnover.
- One position per symbol across strategies. Existing queued ideas reserve risk/capital when proposing additional signals. Actual fills recheck everything; same-day exits do not fund earlier opening purchases.

Risk can exceed a threshold after market moves; controls block new risk rather than promising a hard loss cap. Configuration edits apply to new signals. Existing positions retain their exit configuration. Configuration changes are refused during a scan or while pending signals exist.

## Cost model

Separate modeled charges: brokerage 0.03% capped at INR 20 per order; delivery STT 0.1% each side; buy stamp duty 0.015%; exchange/IPFT combined 0.00307% from October 2024 (earlier conservative 0.00345% assumption); SEBI 0.0001%; GST 18% on brokerage/exchange/SEBI; INR 20 depository sell fee; adverse slippage 0.05% each side. Depository fee is treated as inclusive; broker/account tariffs can differ. Delivery rates are applied conservatively even to same-day stops. Investor-specific capital-gains income tax is excluded.

Sources: [NSE levies](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies), [NSE exchange/IPFT circular effective March 2026](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf). Backtests double slippage, brokerage rate and depository fees in a stress scenario.

## Backtesting and recommendations

[Runner](../screener/swing_backtest.py) evaluates 24 combinations: four independent entry variants, three exit styles and two ATR multiples. It uses the same portfolio engine as the scanner, with dated inputs and no future feature values. Current constituent membership introduces survivorship bias; point-in-time membership is unavailable. The period was examined during earlier work, so the later test is not pristine independent evidence.

After 250-candle warmup, the final 126 sessions are reserved for later testing, the prior 126 for validation, and the earlier sessions for training. Two configurations per entry variant are shortlisted on training return minus drawdown plus twice expectancy-R, requiring 15 closed trades for eligibility. Validation selects one; the selection is saved before later evaluation. Short samples are explicitly ineligible. Walk-forward checks choose parameters using the prior 126 sessions, then evaluate the following 63, twice. Every window starts with an independent flat INR 100,000 account.

Reports include net total/annualized returns, win rate, INR and R expectancy, average winner/loser, profit factor, daily-close drawdown, zero-risk-free Sharpe/Sortino, holding period, costs, open positions, estimated liquidation equity, year-wise and regime attribution, parameter sensitivity, walk-forward checks and cost stress. Boundary years are partial, and annualized short-window results are unstable. Full-period figures are descriptive because they include selection data.

Forward-paper recommendation requires positive training/validation/later expectancy, >=30 later closed trades, positive later return, <=10% drawdown, positive stress return and both walk-forward folds profitable. Passing this is not a live-deployment recommendation or guarantee. Failing strategies are labeled NOT RECOMMENDED; the workspace can still record explicitly experimental paper trades without claiming an edge.

Artifacts: [report](../screener/index_research_cache/swing_expectancy/report.md), [complete results and histories](../screener/index_research_cache/swing_expectancy/results.json), protocol and frozen selections in the same directory. Daily reports support CSV export; account, scans and journal export as JSON. The Backtests tab shows concise later-period comparisons.

## API

Existing portfolio/scan/history/export endpoints remain under `/api/screener/paper`. Added GET `/config`, POST `/config` (complete validated configuration), GET `/research`. No trading or broker execution endpoint is introduced.
