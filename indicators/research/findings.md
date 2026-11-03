# Historical indicator findings

Captured from the original chart's rendered Table View, not from recovered source code. Data: BANKNIFTY 23 Nov 54900 Call, one-minute candles, October 29 11:16 through October 30 15:40, 2026 IST. 651 distinct rows across two sessions; October 29 is partial. The table includes post-15:30 rows, so this is not a validated regular-session dataset. A single row timestamp exists per minute except the overnight gap. Files: [observations](./call_observations.json), [analysis code](./analyze.py), [numerical results](./analysis.json).

## Strong evidence: ribbon calculation

For each adjacent minute, test `ribbon[t] = (5 * ribbon[t-1] + close[t]) / 6`, the EMA(11) recurrence. All 649 comparisons match within 0.011 price units, allowing for both ribbon values being displayed to two decimals. Mean absolute residual: 0.00313; maximum: 0.00834. Open/high/low/hl2/hlc3/ohlc4 alternatives do not match. This identifies the recurrence and source on this sample, not initialization, daily resets, or real-time repainting.

## Strong evidence: states drive markers

The exposed `dash_ribbon` output has values 0 (bearish), 1 (recovery), 2 (bullish), consistent with displayed colors. Captured counts: 350 bearish, 37 recovery, 264 bullish.

All five observed entries into state 2 have a BUY marker. All six observed entries into state 0 have a SELL marker, including a recovery-to-bearish transition. All six entries into recovery have neither marker. No other BUY/SELL markers occur in the captured rows. The first row has no preceding state for comparison.

Examples (IST):

| Date/time | Close | Ribbon | Transition | Marker |
| --- | ---: | ---: | --- | --- |
| Oct 29 12:03 | 817.50 | 811.66 | Bearish → recovery | None |
| Oct 29 12:07 | 835.00 | 819.91 | Recovery → bullish | BUY |
| Oct 29 12:20 | 810.40 | 821.47 | Bullish → bearish | SELL |
| Oct 30 11:09 | 835.35 | 818.90 | Bearish → recovery | None |
| Oct 30 11:13 | 850.50 | 834.47 | Recovery → bullish | BUY |
| Oct 30 12:39 | 903.30 | 919.87 | Bullish → bearish | SELL |
| Oct 30 12:45 | 931.15 | 923.40 | Bearish → recovery | None |
| Oct 30 13:05 | 879.10 | 912.41 | Recovery → bearish | SELL |

This is a useful state-machine skeleton, but the conditions selecting the states have not been recovered. Recovery can last 20 minutes, and a SELL can follow recovery without an intervening BUY. Merely suppressing repeated SELLs using the last BUY/SELL direction would miss that behavior.

## Rejected or unconfirmed hypotheses

- Classifying bullish whenever close exceeds EMA(11), bearish otherwise matches only 258 of 451 bars after excluding the first 200 observations as warm-up (57.2%). This compares states, not trading returns, and cannot represent recovery.
- A scan of slower EMA lengths produces closer fits but no exact explanation; these are exploratory fits on the same sample, not independent validation.
- The first draft's price-plus-one-bar-EMA-slope logic is also inconsistent with the last row: close 760.35 exceeds ribbon 757.56, and ribbon rises from 757.00, yet original state remains bearish. Do not treat that prototype as a faithful signal replica.

## TTC correction

The exposed `t_base` is not the visually estimated square-grid center. It is 968.05 on the captured Oct 29 portion, 962.05 at Oct 30 09:15, and 968.05 from Oct 30 12:05. The latter is an intraday change, so assuming one fixed daily base would be premature. The original meaning of this output and its relationship to the displayed square grid remain unresolved. The half-integer-square spacing observation still holds for the visible rounded level labels.

## What to test next

Capture the put chart as a separate validation dataset; test candidate state rules on calls and then score puts without retuning. Investigate slower trend filters, persistence/confirmation conditions, and recovery thresholds. Compare TTC base changes with explicit breakout outputs and exact displayed base labels. Historical results cannot establish when a live marker first appeared or whether it repaints, and none of these checks establishes profitability.
