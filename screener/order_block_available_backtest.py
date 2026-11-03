"""Replay the current five-minute strategy on complete locally saved sessions."""
import json
from pathlib import Path
import pandas as pd
from backend.indstocks_monitor import completed_bars
from backend.order_block_strategy import signals, STRATEGY, PARAMS
from screener.order_block_backtest_audit import replay


def main():
    root = Path('backend/data')
    sessions, sources = {}, []
    for path in sorted((root/'indstocks_history').glob('*.json')):
        sources.append(str(path))
        for code, payload in json.loads(path.read_text()).get('data', {}).items():
            candles = payload.get('candles', [])
            if not candles:
                continue
            end = pd.Timestamp(max(c['ts'] for c in candles), unit='s', tz='UTC')+pd.Timedelta(minutes=1)
            one, _ = completed_bars(candles, end)
            for day, group in one.groupby(one.index.date):
                key = (code, str(day))
                if key in sessions:
                    merged = pd.concat([sessions[key], group])
                    if any(len(g.drop_duplicates()) > 1 for _, g in merged.groupby(level=0)):
                        raise ValueError('Conflicting saved candles')
                    group = merged[~merged.index.duplicated()]
                sessions[key] = group.sort_index()
    daily, trades, excluded = [], [], []
    for (code, day), one in sorted(sessions.items(), key=lambda item: (item[0][1], item[0][0])):
        start = pd.Timestamp(day+' 09:15', tz='Asia/Kolkata')
        expected = pd.date_range(start, periods=375, freq='min')
        if not expected.equals(one.index):
            excluded.append({'date':day, 'contract':code, 'bars':len(one), 'reason':'INCOMPLETE_SESSION'})
            continue
        five = one.resample('5min').agg(dict(Open='first', High='max', Low='min', Close='last', Volume='sum')).dropna()
        now = start.normalize()+pd.Timedelta(hours=15, minutes=30)
        zones = signals(five, now)
        # Prefix invariance: adding later candles cannot change actionable earlier zones.
        for cut in (start+pd.Timedelta(hours=2), start+pd.Timedelta(hours=4)):
            prefix = signals(five[five.index+pd.Timedelta(minutes=5)<=cut], cut)
            assert prefix == [z for z in zones if pd.Timestamp(z['detected_at'])<=cut]
        result, _ = replay(one, zones, min_risk=PARAMS.min_stop_points, max_risk=PARAMS.max_stop_points)
        for t in result:
            t.update(contract=code, session=day)
        trades.extend(result)
        closed = [t for t in result if 'points' in t]
        daily.append({'date':day, 'zones':len(zones), 'trades':len(closed), 'wins':sum(t['points']>0 for t in closed),
                      'losses':sum(t['points']<0 for t in closed), 'points':round(sum(t['points'] for t in closed),2),
                      'r':round(sum(t['r'] for t in closed),3)})
    closed = [t for t in trades if 'points' in t]
    gains = sum(max(t['r'],0) for t in closed)
    losses = -sum(min(t['r'],0) for t in closed)
    report = {'strategy':STRATEGY,'params':vars(PARAMS),'sources':sources,'daily':daily,'excluded':excluded,'trades':trades,
              'summary':{'sessions':len(daily),'trades':len(closed),'wins':sum(t['points']>0 for t in closed),
                         'points':round(sum(t['points'] for t in closed),2),'r':round(gains-losses,3),
                         'profit_factor_r':round(gains/losses,3) if losses else None,'unresolved':len(trades)-len(closed)},
              'limitations':['Underlying futures OHLC simulation, not option P&L', 'No fees or slippage',
                 'Stop first on ambiguous OHLC; entry-bar targets ignored unless filled at open',
                 'Does not replay quote availability, websocket gaps, or option depth',
                 'Only complete 375-minute sessions included; historical research sample, not independent validation']}
    output = root/'order_block_available_backtest.json'
    output.write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps({k:report[k] for k in ('daily','excluded','summary')},indent=2))

if __name__ == '__main__':
    main()
