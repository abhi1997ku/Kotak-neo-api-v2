"""Paper-only background worker. Start explicitly with python -m backend.intraday_paper_worker."""
import json
import sqlite3
import time
import csv
import io
import copy
import requests
import pandas as pd
from backend.indstocks_data import client
from backend.intraday_stock_store import ROOT, dashboard
from backend.intraday_stock_mapping import build
from backend.indstocks_monitor import completed_bars
from screener.intraday_stocks import rank, entry_signals
from screener.intraday_execution import new_account, queue, process, equity


def write(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


def refresh_mapping(now):
    path = ROOT/'universe.json'
    if path.exists() and json.loads(path.read_text())['as_of'] == str(now.date()):
        return
    for source in ('equity', 'index'):
        response = requests.get('https://api.indstocks.com/market/instruments', params={'source': source},
                                headers={'Authorization': client.token()}, timeout=(5, 20), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('Instrument master unavailable')
        rows = list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff'))))
        if not rows or 'SECURITY_ID' not in rows[0]:
            raise ValueError('Invalid instrument master')
        write(ROOT/(source+'_master.json'), rows)
    build()


def update_frames(mapping, symbols, frames, now):
    codes = {mapping['benchmark']: '__benchmark__', **{mapping['stocks'][s]: s for s in symbols}}
    start = now.normalize()+pd.Timedelta(hours=9, minutes=15)
    for offset in range(0, len(codes), 5):
        batch = list(codes)[offset:offset+5]
        response = client.history(batch, int(start.timestamp()*1000), int(now.timestamp()*1000))
        for code in batch:
            symbol = codes[code]
            candles = response['data'].get(code, {}).get('candles', [])
            if not candles:
                raise ValueError('Missing current candles')
            current, _ = completed_bars(candles, now)
            old = frames.get(symbol)
            if old is None:
                path = ROOT/'history'/(code+'.json')
                if path.exists():
                    old, _ = completed_bars(json.loads(path.read_text()), now)
            merged = pd.concat([old, current]) if old is not None else current
            frames[symbol] = merged[~merged.index.duplicated(keep='last')].sort_index()
            saved = frames[symbol]
            export = saved[['Open','High','Low','Close','Volume']].rename(columns=dict(Open='o',High='h',Low='l',Close='c',Volume='v')).copy()
            export['ts'] = saved.index.as_unit('s').asi8
            candles = export.to_dict('records')
            write(ROOT/'history'/(code+'.json'), candles)


def cycle(frames, account):
    now = pd.Timestamp.now(tz='Asia/Kolkata')
    if now.weekday() >= 5 or not '09:15' <= now.strftime('%H:%M') <= '15:35':
        return dict(state='OUTSIDE SESSION', checked_at=now.isoformat())
    refresh_mapping(now)
    mapping = json.loads((ROOT/'universe.json').read_text())
    dashboard()
    with sqlite3.connect(ROOT/'journal.sqlite') as db:
        snapshots = [json.loads(r[0]) for r in db.execute('SELECT payload FROM snapshots WHERE day=? ORDER BY slot', (str(now.date()),))]
        for slot in ('09:45', '11:00'):
            cutoff = pd.Timestamp(str(now.date())+' '+slot, tz='Asia/Kolkata')
            if cutoff > now or any(s['slot'] == slot for s in snapshots):
                continue
            update_frames(mapping, list(mapping['stocks']), frames, now)
            rows, excluded = rank({s: frames[s] for s in mapping['stocks']}, frames['__benchmark__'], cutoff)
            observed = pd.Timestamp.now(tz='Asia/Kolkata')
            snapshot = dict(day=str(now.date()), slot=slot, rows=rows, excluded=excluded,
                            observed_at=observed.isoformat(), historical_reconstruction=observed-cutoff > pd.Timedelta(minutes=2),
                            universe_count=len(mapping['stocks']), status='WATCH' if rows else 'NO TRADE')
            db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?)', (str(now.date()), slot, json.dumps(snapshot)))
            snapshots.append(snapshot)
    eligible = [s for s in snapshots if not s['historical_reconstruction']]
    chosen = eligible[-1]['rows'] if eligible else []
    active = {t['symbol'] for t in account['trades'] if t['status'] == 'OPEN'}
    pending = {o['symbol'] for o in account['orders'] if o['status'] == 'QUEUED'}
    symbols = sorted(active | pending | {r['symbol'] for r in chosen})
    now = pd.Timestamp.now(tz='Asia/Kolkata')
    if symbols:
        update_frames(mapping, symbols, frames, now)
        cutoff = now.floor('min')-pd.Timedelta(minutes=1)
        first = pd.Timestamp(account['last_bar'])+pd.Timedelta(minutes=1) if account['last_bar'] and active else cutoff
        for stamp in pd.date_range(first, cutoff, freq='min'):
            bars = {s: frames[s].loc[stamp].to_dict() for s in symbols if stamp in frames[s].index}
            process(account, bars, stamp)
        # New signals use actual observation time; historical confirmations cannot fill.
        for signal in entry_signals(frames, frames['__benchmark__'], chosen, now):
            queue(account, signal, pd.Timestamp.now(tz='Asia/Kolkata'))
    account['equity'] = equity(account)
    write(ROOT/'paper_account.json', account)
    return dict(state='RUNNING', checked_at=pd.Timestamp.now(tz='Asia/Kolkata').isoformat(),
                shortlist=len(chosen), message='No timely shortlist today' if not chosen else 'Monitoring confirmed setups')


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = sqlite3.connect(ROOT/'worker_lock.sqlite', timeout=0)
    lock.execute('CREATE TABLE IF NOT EXISTS worker(id INTEGER)')
    lock.commit()
    lock.execute('BEGIN EXCLUSIVE')
    path = ROOT/'paper_account.json'
    account = json.loads(path.read_text()) if path.exists() else new_account()
    frames = {}
    while True:
        try:
            updated = copy.deepcopy(account)
            status = cycle(frames, updated)
            account = updated
        except Exception as exc:
            status = dict(state='PAUSED', error_type=type(exc).__name__, checked_at=pd.Timestamp.now(tz='Asia/Kolkata').isoformat())
        write(ROOT/'worker_status.json', status)
        time.sleep(5)


if __name__ == '__main__':
    main()
