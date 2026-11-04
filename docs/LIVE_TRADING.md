# Live and paper workspaces

The default terminal is live trading only: Market indices, Positions, Order Book,
and Option Chain. Nifty opens initially; select another index to change the chain.
Orders still require submission through the order ticket.

| Workspace | UI | Backend |
| --- | --- | --- |
| Live | http://localhost:5173/ | `backend.live_app:app`, port 8001 |
| Paper and research | http://localhost:5174/?workspace=paper | `backend.indstocks_app:app`, port 8003 |

From the repository root in PowerShell:

```powershell
.\run_servers.ps1 -Action start -Workspace live
.\run_servers.ps1 -Action start -Workspace paper
```

Use `status`, `stop`, or `restart` with the same workspace argument to manage it.
The desktop Start/Stop Trading Terminal shortcuts manage both workspaces.
They call the launcher with `-Workspace all` (ports 5173, 8001, 5174, and 8003).
Start leaves running servers alone and starts missing ones.
The launcher builds and serves each frontend with Vite preview, using separate
`dist-live` and `dist-paper` output folders. This removes development hot reload
and reconnect-triggered development reloads from trading sessions. Stop and Start
once to replace an already running development server; restarting clears broker
logins. Ordinary account polling continues without reloading the page.
Use the new Start/Stop Paper Research shortcuts to manage paper independently.
The Paper & research navigation link opens a separate tab. Both paper servers must
be running; the link does not start server processes. On machines that restrict
PowerShell scripts, use the shortcuts (which set execution policy for that process)
instead of directly invoking the script.
For an already running old backend, restart the live workspace to activate isolation.
Restarting ends its in-memory login; do this outside active trading.

The live backend keeps its Kotak login in memory. The paper backend uses the
separate `INDSTOCKS_ACCESS_TOKEN` market-data connection and exposes no Kotak login
or broker-order routes. The launcher also starts the intraday and option paper
workers; stopping the paper workspace stops both workers. Do not add Uvicorn
workers to the live backend: authentication is stored in process memory.

`backend.main:app` is an older combined application and is not launched by the
desktop controls. The live process does not import its research routers or run
the daily stock paper service. Closing a browser tab does not stop a server or
paper worker; use the desktop Stop shortcut.

For installation, updates and restoring after a PC reset, follow
[README.md](../README.md) and the [recovery guide](WINDOWS_RECOVERY.md).

Indices-only snapshots and streams skip Nifty 50 constituent downloads, cash-market
master resolution, stock quotes, and stock subscriptions. The order ticket closes
on broker acknowledgement, while account refreshes run in the background; repeated
submission is blocked while a request is pending. An acknowledgement is not a fill.
Limit orders can remain pending until the market reaches their price.

Successful option-contract searches are cached in memory for five minutes per
segment, symbol, expiry, and Indian trading date. Prices are fetched on every chain
opening and then updated by the stream; broker errors are not cached. A first-time
contract lookup still requires the broker's instrument-master download.

After the selected chain loads, the UI preloads the other two indices sequentially.
Hovering or focusing an index also prepares its chain. Switching can show the cached
snapshot immediately, with a "Refreshing snapshot" label while fetching fresh data.
Only the displayed option chain has a WebSocket subscription. This improves display
latency, not broker execution latency; a cold first load still waits for the backend.

These changes remove local contention and a post-submission UI wait. Broker/network
latency and exchange fill time are not guaranteed. No real orders are needed for the
automated tests. Measure live acknowledgement and fill latency during market hours
before relying on the terminal for scalping.

Order placement uses a dedicated two-thread executor, separate from quote/account
HTTP workers. The SDK reuses a thread-local HTTP session to retain TCP/TLS connections
and has automatic transport retries disabled. The first request on each worker may
still establish a connection, and idle broker connections may expire.

After a successful submission, the live screen shows the order ID, total browser
round-trip time, backend queue time, and broker-wrapper request time. These measure
acknowledgement, not execution/fill time. Account refreshes happen independently.
