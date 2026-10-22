# Kotak forward paper test

Log into the existing Kotak terminal, then use **Start paper test** in the
index swing panel. This starts one backend-owned connection subscribed only
to Nifty 50 and Nifty Bank. Multiple browser panels share the same tracker.
Closing a panel does not stop collection; Stop, logout or backend shutdown does.
No order API is called and broker positions are not imported or modified.

The separate historical replay remains available below the live panel. Its
statistics are not forward-test results. Live paper trades are exported using
`/api/index-swing/live/export` and saved in
`screener/training_data/live_swing.sqlite3`. The database stores completed minute
candles, setups, entry/exit events, stop updates and feed interruptions. Back up
the database with the backend stopped. Git ignores the training-data folder.

## Timing and data

- Historical hourly context spans prior sessions (roughly one year from Yahoo).
  Saved live hours take precedence. Completed historical minute candles seed
  EMA/volatility for a flat startup; saved open positions retain their exact
  indicators and stop. No historical trades are imported as forward fills.
- Exchange timestamps must be recognized Unix seconds/milliseconds/microseconds/
  nanoseconds within five seconds of receipt. Unknown/stale encodings are rejected
  and counted on the panel; they are not substituted with receipt timestamps.
- Only weekday regular-session ticks (09:15–15:30 IST) are eligible. There is no
  exchange holiday calendar; off-session snapshots cannot create entries.
- First ticks arriving more than five seconds into a minute make that minute
  partial. Partial candles are discarded. This is observed-tick OHLC, not an
  assertion that every exchange update was received.
- EMA readiness requires 30 completed minutes, which can come from historical
  context. There is no routine daily reset. New hourly setups need all 60 minute
  candles, including available bootstrap minutes for a partial startup hour.
  Genuine missing candles still delay eligibility; the existing hourly gap
  checks remain. Start before the session for clean coverage.
- Stops are tested on each fresh accepted tick. EMA changes activate only after
  a completed minute. Stop exits use the observed tick price, including gaps;
  hypothetical costs are two basis points per side.
- Stops, positions, EMA state, partial candles and cooldown are checkpointed.
  Disconnect/logout/manual stop preserves state. Normal overnight/weekend
  restart resumes against the first opening tick, with a 15-second grace period.
  A missed weekday is not assumed to be a holiday.
- Missing trading time with an open position sets RECONCILIATION_REQUIRED.
  The backend automatically requests completed Yahoo minute candles, at most
  once per index per 30 seconds, while continuing to buffer Kotak ticks.
  Recovery is limited to seven calendar days. Every expected weekday regular
  session minute is required; a holiday without calendar evidence stays paused.
- Recovery starts from the saved EMA boundary, checks the existing stop before
  computing the next one, and never loosens a stop. Complete buffered live
  minutes can bridge Yahoo publication latency. Resumption requires a fresh,
  continuously observed current-minute prefix. Offline entries are not created.
- A stop touch in the partially observed disconnect minute or an entry-minute
  extreme that may predate entry is ambiguous and remains paused. Missing,
  malformed, duplicated or stale data also cannot force a resume. The panel
  displays the reason; retries do not change the saved position while blocked.
- Recovery outcomes, checkpoints and the source candles commit atomically.
  Reconstructed full hours persist for subsequent context. A recovered trade
  (even one later closed on live ticks) is labelled
  MIXED_FORWARD_AND_RECOVERED_PAPER and excluded from uninterrupted forward
  training eligibility. Historical stop fills are candle estimates, not broker
  executions. Buffered tick exits retain their observed timestamps.

## Validation boundary

Unit tests cover deterministic synthetic ticks, partial candles, freshness,
minute/hour timing, stop fills, overnight gaps, long/short reconciliation,
ambiguous/incomplete gaps, automatic scheduling and transactional recovery. A real
regular-session run is still required to validate this account's timestamp
encoding, feed continuity and signal operation. Paper results do not measure
broker execution/slippage or establish a profitable strategy from a few trades.


### Persistence and delayed ticks

Each normal engine transition commits its trade records, minute/hour events and
complete checkpoint in one SQLite transaction. A failed transition rolls back
both SQL and in-memory state. Connections are explicitly closed after use.
Restarting therefore restores the corresponding stop, indicators and candle
state together, including entry consumption and exit cooldown.

Ticks older than the last accepted tick or the committed indicator boundary are
rejected and counted. They cannot execute a stop introduced after their exchange
timestamp or recreate a minute already finalized by the heartbeat. This is a
late-data rejection policy, not retrospective fill reconstruction: late prices
are not used to claim executions. Current ticks continue to check the active
stop. Existing gap detection and reconciliation still apply.

Legacy databases with a differing open trade and checkpoint preserve the journal
stop and pause execution. Their indicator boundary is marked unknown because the
old split commits cannot establish a coherent state; automatic reconciliation
must not invent one. Such legacy mismatches require explicit investigation.
