# Stop comparison — 12 October 2026

**Decision: do not replace the active stop logic. Performance improvement is not established.**

No active strategy, parameters, service state or orders were changed in this review.

## What is being compared

| Component | Active opening-trend-v3 | Structure candidate, research only |
|---|---|---|
| Initial stop | 30 option-premium points below entry | For PE: futures resistance plus volatility buffer. For CE: support minus buffer. |
| Trailing | At +30 premium points protect +5; from +60 trail 30 behind peak bid | Tighten using newly confirmed swing levels; never widen an existing stop. |
| Option chain | Bid/ask spread and liquidity gate at entry | Test timestamped OI/volume changes as confirmation separately; no claimed benefit yet. |
| Risk units | Option premium times quantity | Underlying price level triggers an option sale at an observed bid; underlying distance is not premium risk. |

The existing stop already trails. The proposed change is from a premium-based
trail to a market-structure-based exit, not from an entirely fixed stop to a trail.

## Today's isolated structure trace

Use the same 09:18 short signal and next-minute futures open of 55,462. This is
not an option fill. The illustrative rule was fixed before this trace:

- Initial resistance: highest high of the three completed opening candles plus
  0.25 times mean true range over available completed candles, capped at 14 bars.
- Subsequent swing high: a high greater than both preceding highs and at least
  as high as both following highs. It becomes known only after those two following
  candles close. No future bars are used before that confirmation.
- After confirmation, lower the short stop to pivot high plus 0.25 ATR only if
  below the old stop and above the current close. Apply it starting next candle.
- Check the previous stop before changing it. A gap above it uses the candle open
  as the futures exit proxy; otherwise use the stop level. Minute candles cannot
  establish the exact intraminute trigger time or the option bid at that instant.

| Available information | Stop on futures |
|---|---:|
| Entry at 09:18 | 55,627.47 |
| After 09:24 candle closes; 09:22 swing confirmed | 55,435.20 |
| After 09:28 candle closes; 09:26 swing confirmed | 55,349.94 |
| During 09:29 candle | Stop crossed |

Initial futures risk distance: **165.47 points**. Favorable futures movement to
exit proxy: **112.06 points**. These are not rupee profit, option-premium profit,
or evidence of superiority to the active trail. The example also demonstrates
that structure trailing can exit well before the day's trend is finished.
The 0.25 buffer and two-sided pivot confirmation are research assumptions, not
optimized or independently validated settings.

## Missing evidence

The daily audit has 375 usable regular-session futures candles, seven legacy
completed trades, and zero option_mark events. It has no time series of option
bid/ask, OI changes or implied volatility for the 09:18 selected option. Existing
entries/exits for later trades cannot supply its missing intratrade path.

Today's final option chain cannot be applied retrospectively. Also, a fixed
delta conversion does not reconstruct the premium path: option values depend on
volatility and time as well as the underlying. See
[OIC option price behavior](https://www.optionseducation.org/referencelibrary/faq/option-price-behavior).

Consequently, neither the current trail's opening-trade P&L nor the candidate's
option P&L can be measured from the available data. A chain-assisted variant
cannot be replayed at all on today's saved records.

## Fair comparison and adoption gate

First compare exits for identical signals, option contracts, executable entry asks,
and entry times. Track both versions independently through the same timestamped
bid stream. Then run each complete system, allowing its own exits to affect
position occupancy, cooldowns and subsequent entry eligibility. Preserve daily
loss limits and reject any sizing that exceeds the risk budget; do not widen an
open position's risk to accommodate a newly distant structural level.

Compare three separately identifiable arms: current premium trail, structure
trail without chain inputs, and structure plus chain confirmation. OI-heavy
strikes are hypotheses about relevant levels, not guaranteed support/resistance.
Before testing the third arm, define its exact OI threshold, expiry, strike window,
update frequency and stale-data behavior; do not choose these using later prices.

Required data: exact contract identity/expiry, live underlying time and price,
option bid/ask and quote receipt/exchange times where available, strike-level OI
and volume snapshots, spot/futures basis where chain strikes and futures levels
are compared, and actual fee/slippage assumptions. Preserve observations after
the active strategy exits, or the longer-running alternative will be censored.

Adoption requires improved net expectancy after costs on independent sessions,
without worse drawdown/tail losses or risk-budget violations, acceptable data and
execution reliability, and enough observations to assess uncertainty. One day,
especially this already-used development day, is not sufficient evidence.

Until that comparison is possible and passes, **opening-trend-v3 remains unchanged**.
