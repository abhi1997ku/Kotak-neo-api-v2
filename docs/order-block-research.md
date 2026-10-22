# Order-block paper research

This separate experiment uses our own explicit rules, not OrderBlock.com's proprietary data or formula. It does not change EMA/VWAP parameters or place orders.

## Implemented

- Timezone-aware, minute-open OHLCV input; incomplete bars excluded using an explicit as-of time. Invalid prices and duplicate timestamps fail validation.
- A strict swing is confirmed only after the configured number of bars on its right. A directional displacement candle must close through the confirmed swing. The last opposing candle's full range becomes the zone at the displacement candle's close, never at the origin candle's time.
- Only subsequent candles can retest a zone. A wick beyond its far boundary invalidates it conservatively. Zones expire after the configured lifetime and reset each session. Data gaps clear active zones.
- Reference levels are prior supplied session extrema, confirmed swings, and a complete opening range. These are possible liquidity locations, not observed exchange orders. Incomplete prior-session coverage can yield incomplete reference levels; validate input coverage before use.
- Momentum and relative volume contribute 50 points maximum by default. Benchmark-relative strength contributes 30 and futures OI growth 20 only when available. Missing components remain explicitly absent, not imputed. Universe ranking refuses unequal feature coverage or mismatched observation times.
- OI needs an explicitly identified futures series and sample availability timestamps within the corresponding minute. Do not populate these timestamps from retrospective retrieval times or fabricate them. Historical provider OI availability remains unverified.
- Efficiency and VWAP crossings filter chop. Retests require a directional close outside the zone, compatible momentum, volume, entry session, bounded risk, and enough room to the nearest reference target.
- A setup returns a next-minute stop-entry proposal and a stop outside the zone. Two-strike-ITM selection is the intended option policy, not an executed contract selection. This experiment does not yet simulate options, trailing stops, partial exits, or P&L.

## Reproduce

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m screener.order_block_research backend/data/indstocks_validation_7days/raw.json backend/data/order_block_research.json
.\.venv\Scripts\python.exe -m pytest tests/test_order_block.py -q -p no:cacheprovider
```

The artifact records configuration and source SHA-256. The paper UI reads a fixed local artifact through `/api/paper-data/indstocks/order-block-research`; it makes no new market-data requests for this replay.

## First diagnostic: October 12–16, 2026

BANKNIFTY October futures only, 1,875 regular-session minute bars:

| Metric | Result |
| --- | ---: |
| Activated zones | 62 |
| Accepted setups | 1 |
| Rejected retest observations | 157 |
| Insufficient target room | 129 |
| No rejection confirmation | 121 |
| Choppy | 118 |
| Low volume | 109 |
| Momentum conflict | 86 |
| Stop too wide | 51 |
| Outside entry session | 27 |

Rejection reasons overlap; repeated retests of a zone are separate observations. These are not completed trades. OI and benchmark data were absent. A single instrument cannot validate cross-stock rankings. No new strategy performance comparison is claimed.

### Entry outcome checked

The sole accepted setup was long on October 16 at 14:46 IST, with trigger 55,930.05, stop 55,894.95 and target 56,053.00. The next minute did not reach the trigger: **EXPIRED_UNFILLED**. There were zero filled trades, so no realised diagnostic return or option profitability estimate.

The added underlying-only diagnostic uses one-bar stop entries, open-price gap fills, fixed stops/targets, conservative stop-first OHLC ambiguity, a 15:15 time exit, and unresolved outcomes when data is missing. It has no trailing, partial exits, costs, or option execution. This is an execution diagnostic, not the EMA/VWAP management comparison.

On October 18, the read-only INDstocks authentication check rejected the configured token as expired/invalid. Additional history acquisition needs a renewed local token. Existing offline replay remains available.

## Remaining data gates

Collect a timestamped multi-stock universe and aligned benchmark history, verify futures OI semantics, and obtain point-in-time option contracts and executable bid/ask data before testing actual two-strike-ITM outcomes. Freeze rules before evaluating fresh dates. The dashboard is a saved-data research view, not a live stock scanner or automated paper execution engine.
