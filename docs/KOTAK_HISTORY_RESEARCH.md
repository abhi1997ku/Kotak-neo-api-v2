# Isolated Kotak historical-data research

Run from the repository root:

```powershell
.venv/Scripts/python.exe -m screener.kotak_history_probe --start 2026-10-08 --end 2026-10-09
```

The probe reads only `KOTAK_CONSUMER_KEY` from the environment or local `.env`.
It makes GET requests to the official historical endpoint on
`https://mis.kotaksecurities.com`. It does not import the trading SDK, create
a trading session, generate TOTP, submit orders, or modify active candle caches.
No dependencies are installed or upgraded. Redirects are disabled. Results go
to the ignored `screener/index_research_cache/kotak_probe` directory.

The index identifiers are explicitly candidates taken from the existing quote
feed, not verified historical tokens. A documented equity token is a diagnostic
control, never a substitute for Nifty/Bank Nifty data. CSV data remains quarantined
with identity_verified=false until instrument identity is checked.

Initial test for October 8–9, 2026: Nifty returned HTTP 200 without valid candles;
the following Bank Nifty request returned HTTP 429. The probe stopped without
running the equity control. This does not establish whether index history is
supported or how far back it extends. Resolve the historical instrument mapping
and retry only after the broker rate limit clears. 401/403 also stop the probe.

The newer SDK documents a 30-day maximum request span for minute candles,
not guaranteed total retention. Begin with a small verified interval; only then
test older windows. Existing trading logic continues using its existing feed.

Sources:
- [Historical REST API](https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/market-data-apis/historical-data.md)
- [New SDK historical method](https://github.com/Kotak-Neo/kotak-neo-python/blob/main/docs/functions/market_data/historical_data.md)
