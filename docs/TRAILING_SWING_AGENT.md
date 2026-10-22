# Active strategy: hourly entries with 1-minute EMA trailing

The active endpoint now uses [smooth_trailing.py](../screener/smooth_trailing.py).
Entries retain the hourly breakout mathematics documented below. Trailing
defaults to 1-minute candles; 5 minutes is selectable. The entire position
remains open until stopped, with no profit target or partial exits.

After every completed minute calculate EMA21 and exponentially smoothed
14-candle true range V. With peak favorable profit P and initial risk R:

```
buffer = V * (1 + 1.5 * exp(-max(0, P) / R))
long stop = max(previous stop, EMA21 - buffer)
short stop = min(previous stop, EMA21 + buffer)
```

There are no fixed +100/+150 stages in the active strategy. The buffer adapts
smoothly and the stop never retreats. Existing stops are checked before a
candle's close calculates its replacement. A close through the new stop queues
an exit at the next open; opening gaps fill at the actual open.

The dashboard polls every 30 seconds. Real Yahoo minute candles accumulate in
the local ignored research cache; replay starts flat at the available minute
window and displays that window. Missing minute outcomes are excluded, with
EMA warmup reset. Five-minute bars require all five constituent minutes.
This is a simulated candle tracker, not a broker tick stream or placed order.
Statistics are descriptive replay, not independent proof of 80% accuracy.

Tests: `python -m pytest tests/test_smooth_trailing.py -q`.

## Persistent training journal

Hourly support/resistance context is now displayed and included in entry-time
training features. A swing low/high needs two higher/lower neighboring bars
on each side; it becomes known only when the second right-hand candle closes.
Strict extrema are used. Candidates expire after 60 confirmation bars or when
an hourly close breaks them. The nearest qualifying swing level and previous
observed session low/high are compared to the latest hourly close. This layer
is informational: it does not yet alter entries, initial risk, or EMA trailing.

Each successful scan writes all replayed entries, exits and full stop-change
histories to `screener/training_data/swing_trades.sqlite3`, outside the temporary
research cache. Repeated scans deduplicate by strategy version and trade ID;
changed observations are retained as revisions. Entry-time hourly features
are stored separately from later trade outcomes. Open trades and unknown
missing-data outcomes are not marked training eligible. All rows are explicitly
simulated candle replays, not broker executions. Historical backfills have a
separate first-observed timestamp; never interpret that as the signal time.

Download JSONL from `/api/index-swing/history/export`, or add
`?closed_only=true` for completed trades. Both minute intervals retain separate
IDs. Back up the SQLite database when the backend is stopped. The database is
local and ignored by Git. Saving runs on successful scans while the dashboard
polls or the scan API is called; this does not install a background collector.
Future training must split by time and use only entry-time features as inputs;
exit outcomes, stop history and peak profit are labels/post-entry information.

# Archived hourly trailing version

The following describes the superseded hourly stop implementation and its
research command, not the active minute EMA stop.

The dashboard now uses [trailing_swing.py](../screener/trailing_swing.py) through
the existing scan endpoint. The old fixed-3R accuracy gate no longer suppresses
entry plans. This version is an explicit price-action strategy, not a newly
trained ML model or a claim of 80% accuracy.

| Setting | Nifty | Bank Nifty |
| --- | --- | --- |
| Desired swing references (not exits) | 200 / 330 points | 500 / 600 points |
| Trigger-based initial risk range | 60–130 points | 150–260 points |
| Tight / fast activation | +100 / +150 points | +100 / +150 points |
| Position held until stop | 100%; no partial booking | Same |
| Final profit target | Open | Open |

These are planning settings, not predictions. This version uses mathematical
price descriptors instead of EMA agreement. Let W be the median high-minus-low
range of the last twelve completed hourly candles. Fit a straight line to
the twelve closing prices: slope = sum((t - mean(t)) * close[t]) /
sum((t - mean(t))^2). A long setup requires slope/W >= 0.08, a short setup
requires slope/W <= -0.08. Both require path efficiency >= 0.25, calculated as
abs(close - close twelve bars ago) / sum(abs(each of twelve hourly changes)),
and price on the matching side of the eight-hour range midpoint.

An entry triggers beyond the completed eight-hour high/low, plus a buffer of
max(1 point, 0.05 * W). It remains valid for three subsequent full candles unless
the trend reverses. Entries with an opening gap beyond 25% of trigger-based
risk are cancelled. Acceptable gaps increase actual entry risk; the original
stop is not widened. A two-candle post-exit cooldown limits immediate re-entry.

Initial risk is 1.8 * W, clipped to the index settings above. Actual filled
risk includes any acceptable entry gap. Let P be the largest favorable move
observed since entry, and R the actual initial risk in points.

| Stage | Trailing distance D | Additional profit-protection floor |
| --- | --- | --- |
| P < 100 | R | None |
| 100 <= P < 150 | min(R, max(15, 0.75 * W)) | 50% of P |
| P >= 150 | min(R, max(10, 0.50 * W)) | 75% of P |

For a long, the next stop is max(old stop, best high - D, entry + protection).
For a short, use min(old stop, best low + D, entry - protection). Before the
tight stage the initial stop substitutes for the protection floor. The stop
never loosens even if volatility increases. Both +100/+150 thresholds are
configured separately per index in `SETTINGS`, with the same defaults for now.

New stops become effective after the completed hourly candle. If that close
has already reversed through the new stop, queue a full exit at the next
available bar open; never fabricate an earlier fill at the new stop. An
existing stop breached by an opening gap fills at that worse opening price.
Protection formulas therefore do not guarantee a realized minimum profit.

The entire position stays open until a stop exit. There are no partial exits,
fixed profit targets, or maximum holding-time exits. The 200/330 and 500/600
point references do not trigger an order or alter the trailing stop.

## Visible states

- **ENTRY ARMED:** a conditional trade with an entry trigger, stop, validity
  and gap cancellation rule. Closed-session plans require checking after open.
- **MODEL POSITION:** the strategy would already have entered in the candle
  replay. Shows entry time, current stop, remaining size and exit/stop history.
  This is not a broker holding or an instruction to enter at an old price.
- **WAITING FOR TREND:** explains the missing alignment/cooldown and displays
  conditional watch levels rather than inventing a trade.
- **STALE data:** keeps historical plans visible while marking them non-actionable.
  Regular-session freshness checks recognize weekends, but do not contain a
  complete exchange holiday calendar.

The dashboard loads automatically and polls every minute. The server caches
for one minute, and stop calculations use completed hourly candles, including
the short closing segment for fill/stop checks only. Yahoo data can be delayed;
this is not a broker-connected tick-by-tick protective stop. No order is sent.

## Replay accounting

Completed-trade profitability uses the full-position exit, with
2 bps of notional cost per side (4 bps round trip). Weighted index points are
per original unit, not currency P&L. Open-position unrealized points remain
separate from closed-trade statistics. A missing candle invalidates any affected
open model trade; that unknown outcome is counted separately instead of being
fabricated. Price-action context is rebuilt after gaps. Results are descriptive
historical replay, not held-out validation or evidence of future accuracy.

```powershell
.venv/Scripts/python.exe -m screener.trailing_swing_research
.venv/Scripts/python.exe -m pytest tests/test_trailing_swing.py -q
```

The research command saves current candles and `trailing_report.json` in the
ignored local `screener/index_research_cache` directory. The old experiment
results remain available in the collapsed **Previous fixed-target research**
section; they do not control this strategy's entries.
