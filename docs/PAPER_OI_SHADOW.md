# OI shadow collection

The paper agent records OI research while running during market hours. It does not change opening-trend-v3 entries, stops, position sizing, or warm-up. No profitability improvement has been established.

- Futures quotes: approximately every 20 seconds plus request time, only with a recently received futures tick.
- Option-chain snapshots: at most once per minute, including selected strikes, contract tokens, expiry, premiums, volume and reported OI availability. This is a limited strike window, not the whole market.
- Journal events: `oi_future_snapshot`, `oi_chain_snapshot`, `oi_signal_shadow`, and `oi_collection_error`, accessible in the existing day audit. Existing 90-day event retention applies.
- Every snapshot stores receipt time and request latency. Exchange OI update timestamps are not available in the normalized adapter, so all classifications are provisional. Unchanged OI does not prove stale OI.
- Futures context compares the nearest valid sample at least 60 seconds earlier, no more than 180 seconds earlier. Baselines reset by trading day and contract and on agent restart. A quote older than 45 seconds or taking over 3 seconds is unavailable. Missing or zero OI and non-advancing volume do not confirm a trend.
- Rising OI with rising/falling futures prices is labelled bullish/bearish participation. Falling OI is labelled possible covering/unwinding. These are research labels, not evidence identifying who bought or sold.
- Option-chain OI is stored for later strike-level analysis; it does not currently generate support/resistance or trailing stops. A missing OI field retains the legacy numeric zero in the chain API but has `call_oi_available`/`put_oi_available` false; absent availability flags also mean unverified.

For a comparison, replay timestamped observations available before each signal, evaluate unchanged entries versus candidate filters on separate sessions, and include option bid/ask fills, costs, drawdown and missed trends. A single session is insufficient evidence to promote the filter. Historical OI cannot be reconstructed from the latest chain; collection starts only after the updated backend is loaded and the paper agent is started.

Calls run in worker threads so the futures event loop stays responsive. Requests still share the broker client and consume API capacity; this observer does not establish a broker-wide rate limit. Cancelling a worker does not forcibly abort an already running synchronous SDK request.
