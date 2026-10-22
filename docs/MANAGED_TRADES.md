# Managed local OCO — implementation checkpoint

> Historical preview-stage notes. The active terminal has now moved to real order
> integration. See [MANAGED_LIVE.md](./MANAGED_LIVE.md) for current behaviour and limits.

## User-requested final behaviour

Market/limit entry with profit trigger + limit and stop trigger + limit. Monitor the
option premium in the live backend. Select one exit atomically, disable the other
local trigger, submit one regular limit exit, and track actual fills. No automatic
repricing. UI refresh/closure must not stop management. Both local triggers depend
on the backend and price feed; they are not broker-held protection.

## Implemented first stage: preview only

- Live option ticket offers an explicit preview toggle, off by default. Selecting
  it replaces real entry submission with a preview request; neither entry nor exit
  is sent to Kotak. Normal manual order behaviour is unchanged.
- Managed Trades panel shows simulated entry/exit state, chosen leg, premium,
  received-tick age, pause/resume and discard controls. Closed panel does not poll.
- Backend price subscription is independent of browser lifetime. No subscriptions
  are created unless there are active previews. Maximum 10 active previews.
- SQLite state survives restart; unfinished previews restart paused. Request IDs
  deduplicate create requests; conflicting payloads are rejected. One active preview
  per instrument/product. Transitions are serialized in process.
- Profit and stop use the contract's LTP, not the underlying index. Quantity is in
  contract units, not lots. Full fills at qualifying LTP are simulated. A price gap
  beyond an exit limit leaves EXIT_PENDING and never switches to the other leg.
- Local tick receive age is checked; source exchange timestamps, depth/liquidity,
  partial fills, slippage and execution latency are NOT simulated or validated.
- Only the live app exposes these preview endpoints. No live-enable flag or broker
  submission adapter exists in this stage, so a configuration change cannot turn
  this preview into real trading.

## Remaining before live execution

1. Instrument master validation: lot size, tick size, contract expiry, freeze limits,
   market hours and eligible products; scope initial release to bought index options.
2. Dedicated order/position event feed with tested schemas, cumulative fills, trade
   IDs, status transitions and reconciliation. Do not use UI polling for execution.
3. Durable intent/outbox and account identity; persist client IDs before submission,
   distinguish broker rejection from unknown submission outcome, reconcile before
   retry. Preview UUID handling is not broker idempotency.
4. Partial-entry policy: protect fills immediately, cancel unfilled entry when an
   exit wins, reconcile late entry fills and outstanding exit quantities. Simulated
   full-fill logic is not a substitute for this lifecycle.
5. Serialize manual exits, external position changes and automated actions. A single
   strategy must own the position quantity; detect other orders/positions before entry.
6. Backend/session restart and feed-gap recovery. Require broker reconciliation and
   current prices before rearming; handle stale/duplicate/out-of-order events. Persist
   account ownership and do not carry triggers into another account or trading day.
7. Exit rejection/partial-fill/timeout/cancel policy and explicit operator recovery.
   No silent retries or rearming the other leg after an ambiguous submission.
8. Shared broker request budget, prioritization of exit actions and measured tick-to-
   submission/acknowledgement latency. Separate ports do not establish separate quotas.
9. Mock-broker lifecycle and fault-injection tests, then an explicitly authorized
   controlled live validation. No real orders were authorized for testing this stage.

## Validation checkpoint — 2026-10-21

37 tests passed across managed preview engine/lifecycle, live workspace isolation,
order submission worker and authentication error suites. Tests use fake feeds and
mock brokers; no real broker orders or authenticated live streams were exercised.
Frontend TypeScript and production build passed in the implementation stage.

Covered concurrent repeated ticks, immutable winning exit, gap beyond stop limit,
stale/invalid ticks, duplicate request IDs, conflicting requests, restart pause,
short direction, discard/pause/resume, malformed feed prices, session replacement,
disconnect with pending exit, idle subscription suppression, stream shutdown,
nonblocking slow storage, route authentication, and mock order submission without
automatic retry. Review found and fixed synchronous database reads in the service
loop and malformed-price stream termination.

These tests do not establish real fill quality, exchange timestamp freshness,
actual feed reconnect correctness, broker latency, partial-fill handling or live
execution readiness. Browser interaction and a deployed authenticated preview
remain to be exercised. Automated real orders remain unavailable.

## Deployment notes

Frontend builds validate source but do not replace already-running production
previews. Use the desktop Stop/Start launchers only when safe to interrupt sessions.
Restart pauses unfinished managed previews. Never mistake these for protection of
real positions. Existing broker orders are not touched by preview controls.
