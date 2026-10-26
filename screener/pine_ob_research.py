"""Research translation of wugamlo's Order Block Finder (MPL-2.0).

Detection formulas derived from the user-supplied Pine v4 source.
No broker orders. Origin timestamps are not actionable detection timestamps.
"""
import json
from pathlib import Path
import pandas as pd
from backend.indstocks_monitor import completed_bars


def detect(bars: pd.DataFrame, periods: int = 5, threshold: float = 0,
           usewicks: bool = False) -> list[dict]:
    """Evaluate Pine offsets at each current bar open using prior closed bars only."""
    result = []
    for i in range(periods + 1, len(bars)):
        origin = bars.iloc[i-periods-1]
        following = bars.iloc[i-periods:i]
        move = abs(origin.Close-following.iloc[-1].Close)/origin.Close*100
        bull = origin.Close < origin.Open and (following.Close > following.Open).all()
        bear = origin.Close > origin.Open and (following.Close < following.Open).all()
        if move < threshold or not (bull or bear):
            continue
        high = origin.High if bear or usewicks else origin.Open
        low = origin.Low if bull or usewicks else origin.Open
        result.append({'origin_at': bars.index[i-periods-1].isoformat(),
                       'detected_at': bars.index[i].isoformat(),
                       'side': 'long' if bull else 'short', 'high': float(high),
                       'low': float(low), 'midpoint': float((high+low)/2),
                       'move_percent': float(move)})
    return result


def main() -> None:
    source = Path('backend/data/indstocks_history/2026-10-19_23.json')
    candles = json.loads(source.read_text())['data']['NFO_48699']['candles']
    end = pd.Timestamp(max(c['ts'] for c in candles),unit='s',tz='UTC')+pd.Timedelta(minutes=1)
    one, five = completed_bars(candles,end)
    report = {'source': str(source), 'coverage': {str(d): len(g) for d,g in one.groupby(one.index.date)},
              'defaults': {'periods':5,'threshold':0,'usewicks':False},
              'scope':'Indicator detection only. No entry/exit rules exist in the supplied Pine script.', 'variants':{}}
    for name,bars in [('1m',one),('5m',five)]:
        zones=detect(bars)
        for cut in [len(bars)//2,len(bars)-10]:
            assert detect(bars.iloc[:cut]) == [z for z in zones if pd.Timestamp(z['detected_at']) < bars.index[cut]]
        daily={str(d):{'bullish':sum(z['side']=='long' and z['detected_at'][:10]==str(d) for z in zones),
                       'bearish':sum(z['side']=='short' and z['detected_at'][:10]==str(d) for z in zones)} for d in sorted(set(bars.index.date))}
        trades = simulate(one,zones)
        closed = [t for t in trades if 'points' in t]
        summary = {'trades':len(closed),'wins':sum(t['points']>0 for t in closed),
                   'gross_points':round(sum(t['points'] for t in closed),2),
                   'gross_r':round(sum(t['r'] for t in closed),3),
                   'unresolved':len(trades)-len(closed)}
        report['variants'][name]={'daily':daily,'zones':zones,'trades':trades,'summary':summary}
    report['simulation_assumptions'] = ['Added research rules, not part of the indicator',
        'Latest zone per side; replace pending zone when a newer same-side zone appears',
        'Midpoint limit, stop one tick (0.05) beyond zone, fixed 2R from actual fill',
        'Entries 09:30-15:00; same-day detections only; pending zones expire at session end',
        'One position at a time; zones detected while occupied are not queued',
        '15:15 open exit; gaps beyond stop invalidate pending entries',
        'Stop first for ambiguous OHLC; ignore entry-bar target unless filled at open',
        'No fees, option conversion, spread, queue priority or slippage']
    Path('backend/data/pine_ob_detection.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps({'coverage':report['coverage'],'results':{k:v['summary'] for k,v in report['variants'].items()}},indent=2))


def simulate(bars: pd.DataFrame, zones: list[dict]) -> list[dict]:
    """Explicit midpoint-limit experiment, separate from Pine indicator detection."""
    events = {}
    for z in zones:
        events.setdefault(pd.Timestamp(z['detected_at']),[]).append(z)
    pending, trades, position, day = {}, [], None, None
    for at,b in bars.iterrows():
        if day != at.date():
            pending = {}
            if position:
                trades.append({**position,'result':'MISSING_SESSION_EXIT'})
                position=None
            day=at.date()
        if position is None:
            for z in events.get(at,[]):
                if pd.Timestamp(z['origin_at']).date()==at.date():
                    pending[z['side']]=z
        entered_at_open=False
        if position is None and '09:30' <= at.strftime('%H:%M') < '15:00':
            for side,z in list(pending.items()):
                sign=1 if side=='long' else -1
                stop=z['low']-.05 if sign==1 else z['high']+.05
                if sign*(b.Open-stop)<=0:
                    del pending[side];continue
                hit=b.Low<=z['midpoint'] if sign==1 else b.High>=z['midpoint']
                if not hit:
                    continue
                entered_at_open=bool(sign*(b.Open-z['midpoint'])<=0)
                fill=float(b.Open if entered_at_open else z['midpoint'])
                risk=sign*(fill-stop)
                position={**z,'fill_at':at.isoformat(),'fill':fill,'stop':stop,
                          'target':fill+sign*2*risk,'risk':risk}
                pending={}
                break
        if position:
            p=position;sign=1 if p['side']=='long' else -1
            stop_hit=b.Low<=p['stop'] if sign==1 else b.High>=p['stop']
            target_hit=b.High>=p['target'] if sign==1 else b.Low<=p['target']
            exit_price=None
            if at.strftime('%H:%M')>='15:15':
                exit_price=float(b.Open);reason='TIME_EXIT'
            elif stop_hit:
                exit_price=float(min(b.Open,p['stop']) if sign==1 else max(b.Open,p['stop']));reason='STOP'
            elif target_hit and (p['fill_at']!=at.isoformat() or entered_at_open):
                exit_price=p['target'];reason='TARGET'
            if exit_price is not None:
                points=sign*(exit_price-p['fill'])
                trades.append({**p,'exit_at':at.isoformat(),'exit_price':exit_price,'result':reason,
                               'points':round(points,4),'r':round(points/p['risk'],4)})
                position=None
    if position:
        trades.append({**position,'result':'INCOMPLETE_DATA'})
    return trades


if __name__ == '__main__':
    main()
