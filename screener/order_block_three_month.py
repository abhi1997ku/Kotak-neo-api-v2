"""Read-only, resumable three-month BANKNIFTY near-month research download."""
import json,time
from pathlib import Path
import pandas as pd
import requests
from backend.indstocks_data import client,INDstocksError
from backend.indstocks_monitor import completed_bars
from backend.order_block_strategy import signals,PARAMS,STRATEGY
from screener.order_block_backtest_audit import replay
from screener.order_block_variant_comparison import stats
ROOT=Path('backend/data/three_month_research');ROOT.mkdir(exist_ok=True)
START=pd.Timestamp('2026-08-01',tz='Asia/Kolkata');END=pd.Timestamp('2026-11-01',tz='Asia/Kolkata')

def contracts(expired):
    file=ROOT/('expired_contracts.json' if expired else 'current_contracts.json')
    if file.exists():return json.loads(file.read_text())
    params=dict(underlying='BANKNIFTY',segment='DERIVATIVE',instrument_type='FUTIDX',page_size=100,page=1)
    if expired:params.update(expiry_from='2026-08-01',expiry_to='2026-10-31')
    r=requests.get('https://api.indstocks.com/market/instruments/'+('expired/' if expired else '')+'search',params=params,headers={'Authorization':client.token()},timeout=(5,15),allow_redirects=False)
    if r.status_code!=200:raise RuntimeError('Contract lookup HTTP '+str(r.status_code))
    data=r.json()['data'];assert data['count']<=100
    rows=[{**x,'expired':expired} for x in data['instruments']]
    file.write_text(json.dumps(rows));return rows

def main():
    rows=contracts(True)+contracts(False)
    rows.sort(key=lambda x:x['expiry'])
    pieces=[];manifest=[]
    for row in rows:
        expiry=pd.Timestamp(row['expiry'],tz='Asia/Kolkata')
        previous=[pd.Timestamp(x['expiry'],tz='Asia/Kolkata') for x in rows if x['expiry']<row['expiry']]
        a=max(START,max(previous)+pd.Timedelta(days=1) if previous else START)
        b=min(END,expiry+pd.Timedelta(days=1))
        if a>=b:continue
        identifier=row['trading_symbol'] if row['expired'] else 'NFO_'+str(row['security_id'])
        while a<b:
            stop=min(a+pd.Timedelta(days=7),b);path=ROOT/(identifier+'_'+a.strftime('%Y%m%d')+'.json')
            if path.exists():d=json.loads(path.read_text())
            else:
                d=client.history([identifier],int(a.timestamp()*1000),int(stop.timestamp()*1000),expired=row['expired'])
                path.write_text(json.dumps(d));time.sleep(.3)
            cs=(d.get('data',{}).get(identifier) or {}).get('candles') or []
            cs=[c for c in cs if a.timestamp()<=c['ts']<stop.timestamp()]
            manifest.append({'contract':identifier,'from':str(a),'to':str(stop),'candles':len(cs)})
            if cs:
                one,_=completed_bars(cs,stop)
                pieces.append((identifier,one))
            print(identifier,a.date(),len(cs),flush=True);a=stop
    daily=[];trades=[];coverage=[]
    for day in pd.date_range(START,END-pd.Timedelta(days=1),freq='D'):
        selection=[(code,g[g.index.date==day.date()]) for code,g in pieces if (g.index.date==day.date()).any()]
        if not selection:
            if day.weekday()<5:coverage.append({'date':str(day.date()),'status':'NO_DATA_WEEKDAY_HOLIDAY_OR_MISSING'})
            continue
        codes={code for code,_ in selection};assert len(codes)==1
        one=pd.concat([g for _,g in selection]).sort_index();assert one.index.is_unique
        expected=pd.date_range(day+pd.Timedelta(hours=9,minutes=15),periods=375,freq='min')
        if not one.index.equals(expected):coverage.append({'date':str(day.date()),'status':'INCOMPLETE','bars':len(one)});continue
        five=one.resample('5min').agg(dict(Open='first',High='max',Low='min',Close='last',Volume='sum'))
        z=signals(five,day+pd.Timedelta(hours=15,minutes=30))
        ts,_=replay(one,z,min_risk=PARAMS.min_stop_points,max_risk=PARAMS.max_stop_points)
        for t in ts:t.update(session=str(day.date()),contract=next(iter(codes)))
        trades+=ts;daily.append({'date':str(day.date()),**stats(ts,1)})
    report={'strategy':STRATEGY,'params':vars(PARAMS),'start':str(START),'end_exclusive':str(END),'manifest':manifest,'coverage_exceptions':coverage,'daily':daily,'trades':trades,'summary':stats(trades,len(daily)) if daily else {},'limitations':['Underlying futures only; no option P&L, fees or slippage','Missing weekdays require calendar reconciliation; incomplete days excluded','Near-month contract selected from provider expiry dates','Previously inspected October data is not an independent holdout']}
    (ROOT/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'summary':report['summary'],'sessions':len(daily),'coverage_exceptions':coverage},indent=2))
if __name__=='__main__':main()
