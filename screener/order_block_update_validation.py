"""Research validation against production signal generation and stop-floor sensitivity."""
import json
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import pandas as pd
import backend.order_block_strategy as strategy
from backend.indstocks_monitor import completed_bars
from screener.order_block_variant_comparison import zones_for,stats
from screener.order_block_backtest_audit import replay


def main():
    sessions={}
    for p in Path('backend/data/indstocks_history').glob('*.json'):
        for code,v in json.loads(p.read_text()).get('data',{}).items():
            cs=v.get('candles',[])
            if not cs:continue
            end=pd.Timestamp(max(c['ts'] for c in cs),unit='s',tz='UTC')+pd.Timedelta(minutes=1)
            one,_=completed_bars(cs,end)
            for day,g in one.groupby(one.index.date):
                expected=pd.date_range(str(day)+' 09:15',periods=375,freq='min',tz='Asia/Kolkata')
                if g.index.equals(expected):sessions[str(day)]=g
    results=[];parity=0
    for confirmations,floor in [(5,0),(3,0),(3,5),(3,10),(3,15),(3,20)]:
        trades=[];daily=[]
        for day,one in sorted(sessions.items()):
            zones=zones_for(one,5,confirmations,False)
            five=one.resample('5min').agg(dict(Open='first',High='max',Low='min',Close='last',Volume='sum'))
            with patch.object(strategy,'PARAMS',replace(strategy.PARAMS,periods=confirmations)):
                actual=strategy.signals(five,one.index[-1]+pd.Timedelta(minutes=1))
            assert [(z['detected_at'],z['side'],z['midpoint']) for z in zones]==[(z['timestamp'],z['side'],z['entry']) for z in actual]
            parity+=1
            result,_=replay(one,zones,min_risk=floor)
            for t in result:t['session']=day
            trades+=result;daily.append({'date':day,**stats(result,1)})
        row={'confirmations':confirmations,'minimum_stop_points':floor,**stats(trades,len(sessions)),
             'hypothetical_cost_2_points_net_r':round(sum((t['points']-2)/t['risk'] for t in trades),3),
             'hypothetical_cost_5_points_net_r':round(sum((t['points']-5)/t['risk'] for t in trades),3),
             'recent_oct26_29_r':round(sum(t['r'] for t in trades if t['session']>='2026-10-26'),3),
             'daily':daily,'trades_detail':trades}
        results.append(row)
    report={'sessions':sorted(sessions),'production_signal_parity_checks':parity,'results':results,
            'qualification':'Research only. Stop floors reject the setup at first attempted fill, not post-filter trades. Cost scenarios are hypothetical futures points, not option costs. Recent dates already inspected, not holdout data.'}
    Path('backend/data/order_block_update_validation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps([{k:v for k,v in r.items() if k not in ('daily','trades_detail')} for r in results],indent=2))
    print('signal_parity_checks',parity)

if __name__=='__main__':main()
