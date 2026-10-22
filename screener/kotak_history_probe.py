"""Independent read-only historical-data probe. No trading SDK/session imports."""
import argparse
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'screener' / 'index_research_cache' / 'kotak_probe'
URL = 'https://mis.kotaksecurities.com/market-data/1.0/historical/details'


def parse_candles(payload):
    if not isinstance(payload, dict) or str(payload.get('status', '')).lower() != 'success':
        raise ValueError('Broker did not return a successful candle response')
    rows = (payload.get('data') or {}).get('candles')
    if not isinstance(rows, list) or not rows:
        raise ValueError('No historical candles returned')
    if any(not isinstance(row, list) or len(row) < 6 for row in rows):
        raise ValueError('Unexpected candle format')
    frame = pd.DataFrame([row[:6] for row in rows], columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    if any(pd.Timestamp(value).tzinfo is None for value in frame.timestamp):
        raise ValueError('Candle timestamps lack a timezone')
    frame.index = pd.to_datetime(frame.pop('timestamp'), utc=True)
    for column in frame:
        frame[column] = pd.to_numeric(frame[column], errors='raise')
    import numpy as np
    prices = frame[['Open', 'High', 'Low', 'Close']]
    if (not np.isfinite(frame.to_numpy()).all() or (prices <= 0).any().any()
            or (frame.High < prices.max(axis=1)).any() or (frame.Low > prices.min(axis=1)).any()):
        raise ValueError('Invalid OHLCV data')
    if frame.index.has_duplicates:
        raise ValueError('Duplicate candle timestamps')
    return frame.sort_index()


def fetch(key, neosymbol, start, end, get=requests.get):
    if not key:
        raise ValueError('KOTAK_CONSUMER_KEY is not configured')
    if not 0 <= (date.fromisoformat(end)-date.fromisoformat(start)).days <= 29:
        raise ValueError('Use a date range of at most 30 inclusive days')
    response = get(URL, params=dict(neosymbol=neosymbol, interval='1min', fromdate=start, todate=end),
                   headers={'Authorization': key, 'Content-Type': 'application/json'},
                   timeout=25, allow_redirects=False)
    # Never log broker bodies/headers or request exceptions containing credentials.
    if response.status_code != 200:
        return {'http_status': response.status_code, 'result': 'HTTP_ERROR'}, None
    try:
        payload = response.json()
        frame = parse_candles(payload)
    except (ValueError, TypeError, KeyError):
        return {'http_status': 200, 'result': 'NO_VALID_CANDLES'}, None
    return {'http_status': 200, 'result': 'CANDLES_RECEIVED', 'rows': len(frame),
            'first': frame.index[0].isoformat(), 'last': frame.index[-1].isoformat()}, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    key = os.getenv('KOTAK_CONSUMER_KEY') or dotenv_values(ROOT/'.env').get('KOTAK_CONSUMER_KEY')
    if not key:
        raise SystemExit('KOTAK_CONSUMER_KEY missing; configure locally, do not paste it into chat.')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    reports = []
    # Names work for existing index quotes but historical support is unverified.
    # The documented equity token is a control, never a substitute for an index.
    for label, token in [('NIFTY_candidate', 'nse_cm|Nifty 50'),
                         ('BANKNIFTY_candidate', 'nse_cm|Nifty Bank'),
                         ('DOCUMENTED_EQUITY_CONTROL', 'nse_cm|1333')]:
        try:
            report, frame = fetch(key, token, args.start, args.end)
        except requests.RequestException:
            report, frame = {'result': 'NETWORK_ERROR'}, None
        report.update(instrument=label, requested_start=args.start, requested_end=args.end,
                      identity_verified=False, source='Kotak historical API')
        if frame is not None:
            frame.to_csv(OUTPUT/f'{label}_{args.start}_{args.end}.csv')
        reports.append(report)
        print(json.dumps(report), flush=True)
        if report.get('http_status') in (401, 403, 429):
            break
    (OUTPUT/f'probe_{args.start}_{args.end}.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
