# Three-timeframe observer for November 3, 2026

Launch [Start Indicator Observer.cmd](../../Start%20Indicator%20Observer.cmd) before **09:15 IST**, ideally at 09:05. It opens a dedicated Edge window with three chart tabs and a local live dashboard at http://127.0.0.1:18764. The launcher targets November 3 explicitly. There is no scheduled launch: you must start it tomorrow. It can also be started tonight and left running, but login and chart setup must be verified before the open.

## One-time setup in the observer window

1. Sign in to INDstocks in the dedicated Edge window. This uses a separate local browser profile; the existing shared-browser login is not copied. Reload the other chart tabs after signing in if they still show the landing/login page.
2. Choose the **same contract** on all three named tabs. Set `primary_10s` to 10 seconds, `primary_1m` to 1 minute, and `primary_5m` to 5 minutes. Do not change symbols during the session; restart intentionally if a different contract is needed.
3. On each tab, ensure TTC and TRENDIKATERR are added, using the same indicator settings. Right-click an empty chart area and select **Table view**.
4. Check the dashboard. Before the open it says `armed_waiting_for_09_15`; individual streams may show stale data until today's candles arrive. During the session, **all three streams should say `capturing`** and show advancing candle timestamps.

The script validates the indicator table's actual period field, not the tab title. A tab showing the wrong interval is marked `wrong_timeframe` and its data goes to a separate issues file. A contract disagreement within the group is marked `contract_mismatch`. After 09:15, once all three streams are fresh, correctly timed, and agree on their contract, that contract is pinned for the run. Setup before the open remains freely editable; restart to deliberately change an already pinned contract. Metadata is always retained per stream, including any early setup disagreement.

## Capture behavior

- Reads each tab every two seconds using browser automation. This captures the **original indicator's outputs at each timeframe**, not synthetic 1m/5m signals aggregated from 10s data.
- Writes separate append-only files for each stream under `sessions/2026-11-03/`. Records include OHLC, ribbon, states, BUY/SELL markers, TTC outputs, timestamps, metadata, and health. The latest candle remains provisional.
- Flags a chart as stale when its latest bar is older than its timeframe plus 30 seconds. A current receiver heartbeat alone is insufficient. Low-activity contracts can produce stale warnings even if the site is connected.
- Scrolls the virtual table to its newest rows before reading. Requests Chromium background-throttling exemptions; these reduce but do not eliminate the chance of browser/OS suspensions.
- Saves explicit gaps between observed latest-bar timestamps. Some such jumps may be recoverable from overlapping table rows; use final analysis's full observed-bar gaps to quantify missing bars.
- Refreshes per-stream analysis every five minutes and at shutdown. Stops at **15:35 IST**. Afterward the observer-owned Edge window and dashboard close; saved status and analysis remain on disk.
- The process must stay running. Keep its console open, computer awake, network connected, and account logged in. Ctrl+C stops early and preserves saved snapshots. It does not keep Windows awake or auto-login. No orders are placed.
- Reloading a chart does not remove the external observer. Reopen Table View after reload; it resumes once the required table appears. Closing a chart tab is flagged as disconnected; restart to recreate it.

## Optional call and put capture

For six tabs (call and put, each at 10s/1m/5m), run from the repository root:

```powershell
.\.venv\Scripts\python.exe indicators/research/multi_observer.py --date 2026-11-03 --include-put
```

Choose one call contract for all `call_*` tabs and one put contract for all `put_*` tabs. Configure each interval and Table View as above. Run only one observer instance at a time; the dashboard port and dedicated profile are exclusive.

For another day, change `--date YYYY-MM-DD`. Dependency is recorded in [requirements-observer.txt](./requirements-observer.txt); Playwright is already installed in this workspace's virtual environment. Uses the existing installed Edge, no browser download required. The browser profile lives in `%LOCALAPPDATA%/INDstocksIndicatorObserver`; keep it private because the browser may retain your login.

## Verification and limitations

Automated tests cover correct/incorrect intervals, missing schemas, stale prior-day candles, future timestamps, and differing freshness windows for each timeframe. A local Edge smoke test verifies actual DOM table extraction. Live authenticated three-timeframe capture still needs tomorrow's setup and market-session verification. Startup cannot recover missing intrabar history from an earlier period.

Source: [observer implementation](./multi_observer.py). Browser API checked against [Playwright's official browser-launch documentation](https://playwright.dev/python/docs/api/class-browsertype).
