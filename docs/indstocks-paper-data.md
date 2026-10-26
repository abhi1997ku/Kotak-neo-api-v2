# Order-block paper workspace

Active strategy: `order_block_5m_midpoint_2r_v1`.

BANKNIFTY futures, five-minute closed bars. Five consecutive opposite-colour
candles confirm the origin order-block candle. Default minimum move is 0%;
zone uses open-to-low for bullish and open-to-high for bearish blocks.
Detection happens after confirmation, never at the back-plotted origin.

Same-day midpoint retests only. Newer same-side zones replace older pending
zones. One open position; pending zones are cancelled on fill and detections
during an open trade are not queued. Entries 09:30?15:00 IST. Stop 0.05 beyond
the zone; target twice actual underlying fill-to-stop distance. Exit at 15:15.
Restart skips zones detected before startup to avoid retrospective fills.

Options remain two strikes ITM, two lots, nearest unexpired expiry. Streaming
underlying ticks trigger decisions; option depth snapshots estimate fills.
No broker orders. Exchange timestamps unverified and costs excluded.

Active journal: `backend/data/ob_option_paper.sqlite`.
Observations: `backend/data/indstocks_ob_observations.sqlite`.
Old `ema9_option_paper.sqlite` and historical research reports are retained as
archives; they are not read by the active strategy or included in its summary.

Launch: `powershell -File run_servers.ps1 -Action start -Workspace paper`.
Frontend 5174, data-only backend 8003. Live terminal is separate.
