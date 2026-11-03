# November 2 full-session capture

Started at approximately 09:56 IST on November 2, 2026, for the remainder of today's session. The earlier [five-minute live capture](./live_2026-11-02_findings.md) remains separate. This is not a claim of live coverage from market open.

Initial verification captured bearish → recovery at bar 09:56:50 and recovery → bullish/BUY at bar 09:57:10. A live reading received at 09:57:19.258 IST already contained that BUY, before the ten-second bar's nominal end at 09:57:20. This is one sampled event, not proof of a universal signal-confirmation rule. No post-close-proxy revisions were observed in the initial check. The receiver was restarted once during setup; raw records were retained and the combined analyzer records the receipt gap.

The original shared INDstocks tab remains on Table View for NIFTY 03 Nov 23150 Call at the existing 10-second interval. Both TTC and TRENDIKATERR outputs, OHLC, timestamps, and table header metadata are sampled every two seconds. Snapshots are appended immediately to [the raw JSONL file](./day_2026-11-02.jsonl). The local receiver is bound to 127.0.0.1:18763, permits the INDstocks origin, and requires a temporary collector token. It reads no brokerage credentials and has no order code.

The browser timer and local receiver continue after the chat turn ends. Both stop at 15:35 IST today, allowing five minutes after normal market close for final readings. The summary is refreshed every five minutes and once at shutdown. This summary computes descriptive counts, transitions, and revisions; it does not recover or validate the underlying proprietary logic automatically.

- Keep the shared tab open on Table View, preferably active, and the computer awake with a working connection and valid login. Browser background throttling may slow the target cadence; actual receipt gaps are saved.
- Do not reload or navigate this tab. That removes the in-page collector. Closing Table View leaves a heartbeat but records a missing table, and status becomes interrupted.
- Switching symbols or timeframes is recorded in the context header, but uninterrupted observation of the current contract is more useful for inference.
- Use another tab for other browsing or trading. Keep the capture tab visible where possible.
- To stop early, stop the local receiver process that runs `day_capture_server.py`, then reload this tab to clear the timer. Do not stop unrelated Python processes.

Status: [receiver heartbeat and gaps](./day_2026-11-02_status.json). Results: [running analysis](./day_2026-11-02_analysis.json). Check `last_received_ist` and the latest bar timestamps before assuming capture is live. A heartbeat alone does not prove new market ticks arrived.

Rebuild the summary at any time from the repository root:

```powershell
.\.venv\Scripts\python.exe indicators/research/analyze_day.py indicators/research/day_2026-11-02.jsonl
```

Captured observations may include the initial historical rows and a provisional latest candle. Polling can miss changes between samples. A later bar timestamp is a closure proxy. Receiver restarts append to the same raw file; heartbeat counters are per process, while the analysis combines the appended observations. Data gaps and state changes are evidence to inspect, not proof of repainting or profitability.
