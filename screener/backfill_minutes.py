"""Download recent minute history in small requests, preserving source files.

Run: python -m screener.backfill_minutes
These research files do not change the active replay window automatically.
"""
import json
from pathlib import Path
import pandas as pd
from screener.data_fetcher import DataFetcher, yf
from screener.smooth_trailing import minute_bars


def main():
    destination = Path(__file__).resolve().parent / 'index_research_cache' / 'yahoo_backfill'
    destination.mkdir(parents=True, exist_ok=True)
    end = pd.Timestamp.now(tz='Asia/Kolkata').normalize()
    reports = []
    for symbol, ticker in [('NIFTY', '^NSEI'), ('BANKNIFTY', '^NSEBANK')]:
        pieces, failures = [], []
        for offset in (28, 21, 14, 7):
            start, finish = end-pd.Timedelta(days=offset), end-pd.Timedelta(days=offset-7)
            try:
                raw = yf.download(ticker, start=start, end=finish, interval='1m',
                                  ignore_tz=False, auto_adjust=False, progress=False, timeout=20)
                normalized = DataFetcher._normalize_ohlcv(raw, ticker)
                pieces.append(minute_bars(normalized))
            except Exception as exc:
                failures.append({'start': start.isoformat(), 'end': finish.isoformat(), 'error': str(exc)})
        path = destination / f'{symbol}_1m.csv'
        if path.exists():
            saved = pd.read_csv(path, index_col=0)
            saved.index = pd.to_datetime(saved.index, utc=True)
            pieces.insert(0, minute_bars(saved))
        if pieces:
            combined = minute_bars(pd.concat(pieces))
            temporary = path.with_suffix('.tmp')
            combined.to_csv(temporary)
            temporary.replace(path)
            sessions = combined.index.tz_convert('Asia/Kolkata').normalize().nunique()
            report = dict(symbol=symbol, rows=len(combined), sessions=sessions,
                          start=combined.index[0].isoformat(), end=combined.index[-1].isoformat(),
                          missing_minutes_within_observed_sessions=int(sessions*375-len(combined)),
                          failed_requests=failures)
        else:
            report = dict(symbol=symbol, rows=0, failed_requests=failures)
        reports.append(report)
        print(json.dumps(report), flush=True)
    (destination/'coverage.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
