"""Isolated stop/entry comparison. Never changes paper settings or places orders."""
import json
from pathlib import Path

import pandas as pd

from backend.indstocks_monitor import completed_bars
from backend.order_block_strategy import signals
from screener.order_block_backtest_audit import replay
from screener.order_block_variant_comparison import stats

ROOT = Path('backend/data/one_year_research')


def variant_replay(one, zones, buffer_atr=.25, max_risk=120, rejection=False):
    """ATR frozen at setup detection; rejection fills next minute, stop-first OHLC.

    Rejection: touch midpoint and close back on trend side with a directional
    body. The next minute must be contiguous. Quantity is not assumed here.
    """
    pending = {}
    position = None
    armed = None
    trades = []
    by_time = {}
    for z in zones:
        by_time.setdefault(pd.Timestamp(z['detected_at']), []).append(z)
    for at, b in one.iterrows():
        if position is None:
            for z in by_time.get(at, []):
                pending[z['side']] = z
                if armed and armed['zone']['side'] == z['side']:
                    armed = None
        filled_open = False
        if position is None and '09:30' <= at.strftime('%H:%M') < '15:00':
            candidates = [(armed['zone'], True)] if armed and at == armed['at'] else [(z, False) for z in pending.values()]
            armed = None
            for z, confirmed in candidates:
                side = z['side']; sign = 1 if side == 'long' else -1
                atr = z.get('atr')
                if atr is None:
                    continue
                stop = (z['low'] if sign == 1 else z['high']) - sign * max(.05, buffer_atr * atr)
                if sign * (b.Open-stop) <= 0:
                    pending.pop(side, None)
                    continue
                touched = b.Low <= z['midpoint'] if sign == 1 else b.High >= z['midpoint']
                if not confirmed and not touched:
                    continue
                if rejection and not confirmed:
                    breached = b.Low <= stop if sign == 1 else b.High >= stop
                    if breached:
                        pending.pop(side, None)
                    elif sign*(b.Close-b.Open)>0 and sign*(b.Close-z['midpoint'])>0:
                        armed = {'zone': z, 'at': at+pd.Timedelta(minutes=1)}
                    continue
                filled_open = confirmed or sign*(b.Open-z['midpoint'])<=0
                fill = float(b.Open if filled_open else z['midpoint'])
                risk = sign*(fill-stop)
                if not 15 <= risk <= max_risk:
                    pending.pop(side, None)
                    continue
                position = dict(fill_at=at.isoformat(), side=side, fill=fill, stop=stop,
                                risk=risk, target=fill+sign*2*risk)
                pending = {}; armed = None
                break
        if position is None:
            continue
        p = position; sign = 1 if p['side']=='long' else -1
        hit_stop = b.Low<=p['stop'] if sign==1 else b.High>=p['stop']
        hit_target = b.High>=p['target'] if sign==1 else b.Low<=p['target']
        if at.strftime('%H:%M') >= '15:15':
            price = float(b.Open); result = 'TIME_EXIT'
        elif hit_stop:
            price = float(min(b.Open,p['stop']) if sign==1 else max(b.Open,p['stop'])); result='STOP'
        elif hit_target and (p['fill_at']!=at.isoformat() or filled_open):
            price=p['target']; result='TARGET'
        else:
            continue
        points=sign*(price-p['fill'])
        trades.append({**p,'exit_at':at.isoformat(),'exit_price':price,'result':result,
                       'points':round(points,4),'r':round(points/p['risk'],4)})
        position=None
    assert position is None
    return trades


def main():
    source=json.loads((ROOT/'report.json').read_text())
    pieces=[]
    for m in source['manifest']:
        start=pd.Timestamp(m['from']);end=pd.Timestamp(m['to']);code=m['contract']
        data=json.loads((ROOT/(code+'_'+start.strftime('%Y%m%d')+'.json')).read_text())
        candles=[c for c in data['data'][code]['candles'] if start.timestamp()<=c['ts']<end.timestamp()]
        if candles:
            one,_=completed_bars(candles,end);pieces.append(one)
    all_bars=pd.concat(pieces).sort_index()
    assert all_bars.index.is_unique
    five=all_bars.resample('5min').agg(dict(Open='first',High='max',Low='min',Close='last',Volume='sum')).dropna()
    tr=pd.concat([five.High-five.Low,(five.High-five.Close.shift()).abs(),(five.Low-five.Close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14,min_periods=14).mean()
    variants={k:[] for k in ('current','buffer_same60cap','buffer_120cap','rejection_buffer_120cap')}
    for session in source['daily']:
        day=pd.Timestamp(session['date'],tz='Asia/Kolkata');one=all_bars[all_bars.index.date==day.date()]
        zones=signals(five[five.index.date==day.date()],day+pd.Timedelta(hours=15,minutes=30))
        for z in zones:
            value=atr.get(pd.Timestamp(z['detected_at'])-pd.Timedelta(minutes=5))
            z['atr']=float(value) if pd.notna(value) else None
        results={'current':replay(one,zones,min_risk=15,max_risk=60)[0],
                 'buffer_same60cap':variant_replay(one,zones,max_risk=60),
                 'buffer_120cap':variant_replay(one,zones),
                 'rejection_buffer_120cap':variant_replay(one,zones,rejection=True)}
        for key,ts in results.items():
            variants[key].extend([{**t,'session':session['date']} for t in ts])
    assert variants['current']==[{k:v for k,v in t.items() if k!='contract'} for t in source['trades']]
    report={'scope':'Underlying futures research only; NOT option rupee P&L or independent holdout',
            'assumptions':['0.25 times trailing 14 completed 5m mean true range buffer, frozen at detection',
                           '15-point risk floor; cap shown per variant; 2R target; stop-first ambiguous bars',
                           'Rejection uses closed 1m directional bar reclaiming midpoint, next contiguous minute open',
                           'No fees; R assumes equal risk per trade, not fixed option lots; no live settings changed'],
            'variants':{k:{'summary':stats(ts,len(source['daily'])),
                           'halves':{label:stats([t for t in ts if (t['session']<'2026-05-01')==first],sum((d['date']<'2026-05-01')==first for d in source['daily']))
                                     for label,first in [('first_half',True),('second_half',False)]},
                           'trades':ts} for k,ts in variants.items()}}
    (ROOT/'stop_entry_comparison.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:{'summary':v['summary'],'halves':v['halves']} for k,v in report['variants'].items()},indent=2))


if __name__=='__main__':
    main()
