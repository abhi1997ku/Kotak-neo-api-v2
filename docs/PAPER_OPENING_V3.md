# Opening trend v3 — paper experiment

This is a testable implementation, not an established profitable strategy.
The parameters below were chosen as research defaults and not optimized through
a search on the 12 October losing session. That session is development data.

## Readiness and entries

Log in and start the paper agent before 09:15 IST. It can be armed before the open.
At 09:18, the first three completed one-minute candles make the strategy ready
to evaluate an entry. A live next-minute tick is needed to close the third bar.
Readiness does not promise an entry: stale data, wide spreads or mixed conditions
will prevent it. Previous-day history is optional for v3; the 25-candle engine
remains available only as a comparison in research.

The same futures contract's 09:15 bar must be present. A late start without broker
history containing the session open is blocked explicitly. With live data from
the open, a failed historical request no longer imposes a 25-minute wait.

Entry direction requires:

- The latest two closes on the same side of their respective session VWAPs and
  the 09:15 opening price.
- EMA-5/EMA-13 agreement, with EMA-5 moving in the same direction. These EMAs use
  today's available closes, initialized from its first close, not fake extra bars.
- At least 55% directional efficiency over up to ten candles, measured as net
  close movement divided by total close-to-close travel including the first body.
- Distance from VWAP no greater than twice the recent average candle range.
- Three contiguous recent candles with positive aggregate session volume.

Neutral-to-directional or direction-change events produce signals, not repeated
entries each minute. An existing position blocks entry. Re-entry additionally
requires a new signal, ten minutes after the last exit, and available risk budget.
This intentionally does not guarantee capturing every continuation.

## Execution and risk defaults

All orders remain virtual. Buy the second available ITM option strike with three
lots, using a fresh selected-contract ask rather than an aging chain price. Require
a positive non-crossed bid/ask, spread no larger than the lesser of 3 premium points
or 0.5% of ask, and a quote request completed within three seconds. A fast response
is a transport freshness check, not proof of exchange quote age: the current quote
normalizer does not expose a reliable exchange timestamp.

Entry lookup runs separately from futures ingestion. Recheck direction, time,
daily limits and the futures price afterward. Reject lookup older than 20 seconds,
futures ticks older than five seconds, or a move of more than 15 futures points
since the signal. Actual entry can therefore be later than a replay signal or be
skipped entirely. No fabricated fill is used when quotes are unavailable.

- Initial option stop: 30 premium points; maximum planned risk ₹2,700 per entry.
- Daily entry lockout: three trades, two losing trades, or ₹5,400 realized loss.
  Also reject an entry whose initial risk exceeds the remaining daily budget.
- Entry window: 09:18–15:00 IST; market-close exit at/after 15:20 on a usable bid.
- No fixed profit target. At a peak gain of 30 points, stop moves to entry +5.
  From a peak gain of 60, stop follows 30 points below the best observed bid.
  The stop never moves backward. Full quantity exits together; no partial exits.
- Loss limits use gross recorded P&L. Stops are checked on quotes, so slippage,
  gaps and costs can exceed planned risk; these are not guaranteed loss ceilings.

Option bid polling runs independently every two seconds plus request duration.
Timeouts do not block futures candles. Invalid or slow bids are logged as degraded
monitoring rather than invented exits. If the futures stream ends while a position
is open, option monitoring continues. Explicit Stop stops monitoring and retains
the virtual position for resume; restarting the backend requires Start again.

## Audit and restart behavior

The dashboard and startup/signal/entry logs identify `opening-trend-v3`. Every
successful option mark and stop change is journaled. Open position, contract token,
peak bid and stop persist. Entry state and its journal event are committed together;
closing a position clears its saved state with the trade write. Closing the same
trade twice does not duplicate realized P&L. Daily totals and cooldown restore from
the journal. Audit candles/events are retained for 90 days.

## Validation

Behavioral tests cover both opening directions with no seed, mixed/missing opening
data, unbounded runner upside, monotonic trailing, loss budget and cooldown, restart
state, atomic entry failure, idempotent closure, asynchronous quote delays and
stale entry rechecks. Run:

```text
python -m pytest tests/test_paper_trend.py tests/test_paper_trading.py tests/test_paper_history.py tests/test_paper_signal_backtest.py -q
python -m screener.paper_signal_backtest --output screener/index_research_cache/opening_v3_replay
```

The 12 October development replay produced a 09:18 short and a second short signal
at 09:25, with no afternoon signals. The second signal would be blocked if the first
position were still open or within the ten-minute post-exit cooldown. The first
signal's next-open forward futures moves were +135 points at 5 minutes, +164.2 at
15 minutes and +208.6 at 30 minutes. These are not option profits. Option execution,
trailing performance, costs and the loss lockout cannot be reconstructed from the
available candle-only data.

Freeze this version while collecting independent paper sessions. Before any claim
of profitability, evaluate net expectancy after actual fees and spread, maximum
drawdown, losing streaks, fill/rejection rates and uptime across both directional
and sideways sessions. Do not optimize using the same sessions used for evaluation.
NSE's [algorithmic trading curriculum](https://www.nseindia.com/static/learn/self-study-ncfm-modules-advanced-algorithmic-trading-module)
includes transaction-cost analysis, backtesting and risk management as separate
parts of strategy evaluation.
