# Live managed target and stop

## Current behaviour

The live terminal's option order ticket offers **Live target & stop - real orders**.
It is off by default. When selected, submitting sends a real entry to Kotak. Neither
entry nor exit is simulated. Old preview records are in a separate database and
are never converted into live orders. Preview routes are no longer exposed.

Current scope: one active managed trade per account, one or more whole lots of bought NSE Nifty,
Bank Nifty, or BSE Sensex options, MIS or NRML. Sensex uses the BSE contract master,
ordinary BSE expiry timestamps, and BSE quote/order routing. Quantity is in contract units and must be a
positive multiple of the current instrument-master lot size. Broker/exchange
quantity limits still apply; oversized orders are not automatically split. New entries
require a matching current instrument-master token/symbol, valid expiry, 0.05
premium steps, a flat instrument, no pending orders in that instrument, current
premium inside the two triggers, and connected price/order feeds. Weekday entry
window is 09:15–15:15 IST; holiday/exchange acceptance still belongs to Kotak.

1. Persist intent and a stable client order tag before entry submission.
2. Broker acknowledgement supplies an order ID, not a fill. Reconcile cumulative
   fills from the order report. No entry fills are inferred from LTP.
3. Streamed option premiums select profit or stop once. Persist that selection;
   disable the other local trigger. Prices are queued in receipt order so a brief
   crossing is not overwritten by the next price.
4. If entry is partially filled and still open, cancel the unfilled remainder,
   then wait for a terminal entry status and final cumulative fill quantity.
5. Check broker position quantity and external orders, then submit one regular
   sell limit exit at the configured winning limit. Broker fills close the trade.
6. A gap beyond a sell limit can leave EXIT_PENDING. There is no automatic
   repricing, market conversion or second exit order.

Price and order sockets belong to the live backend, independent of browser
lifetime. Order events wake the worker; authoritative REST reconciliation is
scheduled no faster than once per second, with additional reconciliation at exit.
The REST checks add latency; this release does not promise subsecond execution.
Recorded trigger-to-acknowledgement time includes those checks and broker latency.

## Controls and recovery

- **Edit profit & stop:** update both trigger and limit prices while WAITING_ENTRY
  or ARMED, before either exit is selected. Existing protection remains active
  while the form is open. Saving reconciles fills, requires connected feeds and a
  fresh premium inside the new triggers, and persists all four prices together.
  Stale form revisions are rejected. A trailing stop may exceed the original
  entry price. Once an exit has triggered, this edit is blocked.
  Quantity remains tied to the original entry and actual fills; editing prices
  does not place additional buys/sells or change a pending entry's quantity.
- **Reconcile / resume:** refresh broker state. On the same trading day, known
  consistent orders can resume after a backend restart. This can send a previously
  selected exit once the entry's final quantity is confirmed.
- **Exit at stop limit:** selects a manual exit at the existing stop limit. It is
  a real limit order, not a guaranteed fill or market order.
- **Release to manual:** explicitly ends local management. Requires no pending
  broker orders for the instrument and no unresolved submission. Remaining real
  positions must then be managed manually.
- Normal terminal placement/modification is blocked for the managed instrument
  or managed order IDs. Cancellation remains available through the Order Book.
  Changes made through Kotak or another application are detected by reconciliation;
  they are not atomic with this manager. Avoid mixing execution systems on the
  same managed instrument.

Submission timeouts/malformed acknowledgements enter ENTRY_UNKNOWN/EXIT_UNKNOWN.
The manager never resends automatically. It attempts to recover the order by its
persisted tag from the broker report. If Kotak does not return that tag, manual
broker/support verification is required; do not submit another entry to compensate.

Restarted trades are marked RECOVERY. Previous-day trades do not rearm automatically.
Missing/inconsistent quantities, external orders or position mismatches pause
automation. Cancelled/rejected/expired partial exits require manual review rather
than automatic replacement. Account identity is persisted as a hash.

## Operational limits

Both triggers are local, not broker-held stop protection. Backend shutdown, lost
authentication or unavailable/stale feeds can prevent exits. The frontend does
not need to remain open, but the live backend does. An accepted limit order can
remain unfilled. Pending entry cancellation adds another interval before an exit.

Run a single live backend worker. Different frontend/backend ports do not isolate
broker request quotas or external orders. The current implementation validates
local receive age; exchange tick timestamp freshness is not independently verified.
Reconnection uses fresh receipt/reconciliation, not a guaranteed broker replay.

## Validation and deployment

2026-10-26: 73 focused tests passed for the live manager, broker adapter, shared
validation and order submission, including multi-lot quantities, price editing,
stale revisions, persistence, and exit quantity preservation.

2026-10-23: 61 focused tests passed across the live manager, broker adapter, legacy
preview isolation, workspace isolation, order submission and authentication suites.
TypeScript compilation and Vite production build passed. Running servers were not
restarted, and no controlled real-money validation was performed.

Tests use fake brokers and fake streams; no real trades were submitted during
development. They cover acknowledgement-vs-fill, competing ticks, partial entry
cancellation with late fills, unknown submissions across restart, position mismatch,
external orders, rejected partial exits, stale ticks, bad report envelopes, exact
client tags, and session changes. Legacy preview tests remain isolated from the
live runtime. Automated tests do not establish real exchange fill quality or latency.

Restart the live backend and rebuild the production frontend through the desktop
launcher when safe to interrupt the login session. Do not restart with unmanaged
exposure assuming local triggers will keep running. No servers are restarted by
the test suite. A controlled broker validation remains necessary to establish actual
account-specific feed/report behaviour and timing; no such live trial has been run.
