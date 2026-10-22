# v3.1 late-start recovery

On 13 October the agent first recorded 09:17, historical backfill failed, and the strict 09:15 check kept the strategy neutral for the entire session. More candles could never clear that condition.

The paper agent now explicitly enables observed-session recovery when 09:15 is missing. It excludes the first potentially partial minute, requires three consecutive completed observed candles, and computes its VWAP and price anchor from those observations. A gap resets the observed anchor to the contiguous suffix. It never manufactures missing candles or calls the observed anchor the exchange session open.

The normal 09:15 path remains unchanged. The default engine remains strict for historical comparisons; the paper agent opts into recovery. Stops, risk budgets, cooldowns, trade limits and OI shadow behavior are unchanged. The dashboard identifies the fallback mode. Restarted agents reload matching-contract cached bars and still wait for a new completed candle before signaling.

This is a paper-experiment readiness correction, not evidence of better profitability. Existing v3 backtests do not establish v3.1 performance. Functional regression tests cover both directions, the partial first candle, gaps, duplicate suppression, and normal opening behavior.
