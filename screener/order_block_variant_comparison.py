"""Research-only timeframe/confirmation/entry comparison; no paper-engine changes."""
import json
from unittest.mock import patch
from dataclasses import replace
import backend.order_block_strategy as strategy
from pathlib import Path
import pandas as pd
from backend.indstocks_monitor import completed_bars
from backend.order_block_strategy import signals
from screener.pine_ob_research import detect, simulate

VARIANTS = [('5m_5_midpoint',5,5,False),('5m_3_midpoint',5,3,False),
            ('5m_5_edge',5,5,True),('3m_5_midpoint',3,5,False),('1m_5_midpoint',1,5,False)]


def zones_for(one, minutes, confirmations, edge):
    """Only detect from completed, contiguous candles in this session."""
    rule=f'{minutes}min'
    grouped=one.resample(rule,origin=one.index[0])
    bars=grouped.agg(dict(Open='first',High='max',Low='min',Close='last',Volume='sum'))
    bars=bars[grouped.Close.count()==minutes]
    if bars.empty:return []
    next_at=bars.index[-1]+pd.Timedelta(minutes=minutes)
    extended=pd.concat([bars,pd.DataFrame([bars.iloc[-1]],index=[next_at])])
    result=[]
    for z in detect(extended,periods=confirmations):
        at=pd.Timestamp(z['detected_at']);origin=pd.Timestamp(z['origin_at'])
        if at.strftime('%H:%M')>='15:00':continue
        if not pd.date_range(origin,periods=confirmations+1,freq=rule).isin(bars.index).all():continue
        z['entry_level']=z['high'] if edge and z['side']=='long' else z['low'] if edge else z['midpoint']
        # Existing simulator takes its limit from midpoint; preserve original zone midpoint explicitly.
        z['zone_midpoint']=z['midpoint'];z['midpoint']=z['entry_level']
        result.append(z)
    return result


def stats(trades, days):
    closed=[t for t in trades if 'r' in t]
    rs=[t['r'] for t in closed];wins=sum(r>0 for r in rs)
    gains=sum(max(r,0) for r in rs);losses=-sum(min(r,0) for r in rs)
    equity=peak=dd=0
    for r in rs:
        equity+=r;peak=max(peak,equity);dd=max(dd,peak-equity)
    return dict(trades=len(closed),wins=wins,losses=sum(r<0 for r in rs),win_rate=round(100*wins/len(rs),2) if rs else 0,
                trades_per_day=round(len(rs)/days,2),points=round(sum(t['points'] for t in closed),2),
                total_r=round(sum(rs),3),profit_factor_r=round(gains/losses,3) if losses else None,
                max_drawdown_r=round(dd,3),unresolved=len(trades)-len(closed))


def main():
    sessions={}
    for path in sorted(Path('backend/data/indstocks_history').glob('*.json')):
        for code,v in json.loads(path.read_text()).get('data',{}).items():
            cs=v.get('candles',[])
            if not cs:continue
            end=pd.Timestamp(max(c['ts'] for c in cs),unit='s',tz='UTC')+pd.Timedelta(minutes=1)
            one,_=completed_bars(cs,end)
            for day,g in one.groupby(one.index.date):
                key=(str(day),code)
                if key in sessions:
                    merged=pd.concat([sessions[key],g])
                    assert all(len(x.drop_duplicates())==1 for _,x in merged.groupby(level=0)), 'Conflicting candles'
                    g=merged[~merged.index.duplicated()]
                sessions[key]=g.sort_index()
    complete={k:v for k,v in sorted(sessions.items()) if v.index.equals(pd.date_range(k[0]+' 09:15',periods=375,freq='min',tz='Asia/Kolkata'))}
    assert len({k[0] for k in complete})==len(complete),'Multiple contracts for one session'
    report={'sessions':[k[0] for k in complete], 'variants':{},'assumptions':['Closed-bar signals; one-minute OHLC execution for every variant','Same-day zones; latest per side; one position; entries 09:30-15:00; close 15:15','Stop 0.05 outside zone; 2R from fill; stop-first ambiguity; no entry-bar target unless open fill','Gross futures results only; no option premium, fees, slippage, feed outages or depth simulation','Exploratory comparison on previously inspected data, not independent validation']}
    for name,minutes,confirmations,edge in VARIANTS:
        all_trades=[];daily=[]
        for (day,code),one in complete.items():
            zones=zones_for(one,minutes,confirmations,edge)
            for n in (120,240):
                cutoff=one.index[n]
                assert zones_for(one.iloc[:n],minutes,confirmations,edge)==[z for z in zones if pd.Timestamp(z['detected_at'])<=cutoff]
            trades=simulate(one,zones)
            if name=='5m_5_midpoint':
                with patch.object(strategy, 'PARAMS', replace(strategy.PARAMS, periods=5)):
                    baseline=simulate(one,signals(one.resample('5min').agg(dict(Open='first',High='max',Low='min',Close='last',Volume='sum')),one.index[-1]+pd.Timedelta(minutes=1)))
                assert [(t['fill_at'],t.get('r')) for t in trades]==[(t['fill_at'],t.get('r')) for t in baseline]
            for t in trades:t.update(session=day,contract=code)
            all_trades.extend(trades);daily.append({'date':day,'setups':len(zones),**stats(trades,1)})
        report['variants'][name]={'summary':stats(all_trades,len(complete)),'daily':daily,'trades':all_trades}
    Path('backend/data/order_block_variant_comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps({k:v['summary'] for k,v in report['variants'].items()},indent=2))
    print('DAILY',json.dumps({k:[(d['date'],d['trades'],d['total_r']) for d in v['daily']] for k,v in report['variants'].items()}))

if __name__=='__main__':main()
