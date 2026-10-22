# Paper index ticker

Nifty 50 and BankNifty spot cards subscribe to INDstocks `ltp` market-data messages through the backend `/api/paper-data/indstocks/indices-stream` WebSocket. Credentials stay on the backend. No order/update socket is used.

The cards fetch a single REST reference snapshot on mounting (or manual refresh), then replace prices on incoming ticks. There is no recurring five-second REST price polling. Each index shows its own last received time; no tick for 15 seconds, or a disconnected socket, marks the value stale. The connection retries after 30 seconds. A reference snapshot is never labelled streaming.

The existing BANKNIFTY futures quote stream remains separate. Option-paper execution still uses REST depth snapshots; this UI change does not convert option fills to tick-driven execution. Local receipt freshness does not verify exchange quote age.

Source: [INDstocks price feed documentation](https://api-docs.indstocks.com/Websockets/). Stream parser and data-only route tests pass. Deployed on October 20, 2026, a trading holiday; moving-market index ticks still need session validation. On a new trading day, reload the dashboard/reference snapshot for the new previous-close value.
