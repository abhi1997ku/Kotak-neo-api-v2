# BANKNIFTY paper-agent warm-up

The active agent now uses [opening trend v3](./PAPER_OPENING_V3.md), which can
evaluate after three session candles without prior-day history. The description
below documents the earlier 25-candle/seeded engine retained for replay comparison.

The agent requests ten calendar days of completed one-minute candles using the
token of the live BANKNIFTY future. It retains up to 80 candles from the most
recent available prior session plus today's candles. Cache keys include segment,
token and trading symbol so expiry changes cannot reuse another contract's bars.
Old untagged audit candles are not used as contract history.

Historical downloads seed indicators without generating retrospective trades.
The next live candle close can generate a signal. Overlapping and out-of-order
ticks are ignored. Failed or empty history requests fall back to the contract
cache; without enough cached data, the normal 25-candle warm-up remains necessary.
The dashboard and daily audit show the history source and loaded counts.

## Opening-session rule

At least 25 combined price candles and three completed candles from today are
required. Before today's 25th candle, bullish confirmation requires EMA-7 above
EMA-25, the latest close above today's VWAP, VWAP above its moving average, and
the latest close above today's first candle close. Bearish confirmation reverses
these comparisons. This permits an earliest signal around 09:18 IST when history
is available and conditions align; it does not guarantee a signal then.

Only today's volumes and typical prices contribute to VWAP. Its moving average
uses up to 21 available session values. After 25 session candles, the existing
EMA-versus-moving-VWAP regime comparisons apply. A full session fits in the
500-candle indicator buffer together with the 80 prior-session bars.

An initial directional reading is eligible once; unchanged regimes do not keep
generating entries. Restarting with entries already recorded today suppresses
an extra initial entry. This remains a paper experiment, not a validated claim
of improved trading performance.

## Verification and activation

Run `python -m pytest tests/test_paper_trading.py tests/test_paper_history.py -q`.
Tests cover both directions, neutral/no-volume conditions, initial/restart
signals, session isolation, invalid/incomplete historical data, stale ticks,
contract isolation, day reset, and history failure with/without cached bars.

The read-only probe on 2026-10-12 for BANKNIFTY26OCTFUT (48699), using the existing
MIS host, returned no usable one-minute history. The running integration uses the
authenticated session's base URL, as specified in the
[Kotak historical API documentation](https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/market-data-apis/historical-data.md).
Live broker-history availability on that session URL still needs verification.

Restart the backend, log in again (sessions are in memory), then start the paper
agent. Inspect the history message before assuming the shorter warm-up is active.
