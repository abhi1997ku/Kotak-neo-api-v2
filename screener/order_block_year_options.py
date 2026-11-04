"""Historical option-price attribution to frozen underlying signals; no broker orders.
Next-minute open estimates for separate entry/exit minutes. Same-minute round trips
are unresolved in this model and have a separate adverse OHLC scenario, never dropped silently.
"""
import json,time
from pathlib import Path
import pandas as pd
import requests
from backend.indstocks_data import client
ROOT=Path('backend/data/one_year_research');CACHE=ROOT/'options';CACHE.mkdir(parents=True, exist_ok=True)

def get_data(path,params):
    for attempt in range(3):
        r=requests.get('https://api.indstocks.com'+path,params=params,headers={'Authorization':client.token()},timeout=(5,15),allow_redirects=False)
        if r.status_code==200:
            d=r.json()
            if d.get('status')=='success' or d.get('success') is True:return d['data']
        if r.status_code in (401,403):raise RuntimeError('Provider authentication rejected')
        time.sleep(1+attempt)
    raise RuntimeError('Contract lookup failed HTTP '+str(r.status_code))

def chain(expiry):
    p=CACHE/('chain_'+expiry+'.json')
    if p.exists():return json.loads(p.read_text())
    expired=expiry<'2026-10-31'
    if expired:
        d=get_data('/market/instruments/expired/contracts',dict(underlying='BANKNIFTY',segment='DERIVATIVE',instrument_type='OPTIDX',expiry=expiry))
        rows=d if isinstance(d,list) else d['instruments']
    else:
        rows=[];page=1
        while True:
            d=get_data('/market/instruments/search',dict(underlying='BANKNIFTY',segment='DERIVATIVE',instrument_type='OPTIDX',expiry=expiry,page=page,page_size=100))
            rows+=d['instruments']
            if len(rows)>=d['count']:break
            page+=1;time.sleep(.3)
    p.write_text(json.dumps(rows));return rows

def history(ids,start,end,expired,path):
    if path.exists():return json.loads(path.read_text())
    for attempt in range(3):
        try:
            d=client.history(ids,int(start.timestamp()*1000),int(end.timestamp()*1000),expired=expired)
            path.write_text(json.dumps(d));time.sleep(.3);return d
        except Exception:
            if attempt==2:raise
            time.sleep(1+attempt)

def select(rows,spot,side):
    kind='CE' if side=='long' else 'PE'
    candidates=[x for x in rows if x.get('option_type')==kind]
    strikes=sorted({float(x['strike_price']) for x in candidates})
    atm=min(strikes,key=lambda x:(abs(x-spot),x));index=strikes.index(atm)+(-2 if kind=='CE' else 2)
    if index<0 or index>=len(strikes):raise ValueError('Missing strike ladder')
    return next(x for x in candidates if float(x['strike_price'])==strikes[index])

def summary(rows,key):
    values=[r[key] for r in rows if r.get(key) is not None]
    equity=peak=drawdown=0
    for v in values:equity+=v;peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    losses=-sum(min(x,0) for x in values)
    return dict(trades=len(values),wins=sum(x>0 for x in values),losses=sum(x<0 for x in values),breakeven=sum(x==0 for x in values),pnl=round(sum(values),2),profit_factor=round(sum(max(x,0) for x in values)/losses,3) if losses else None,max_closed_trade_drawdown=round(drawdown,2))

def historical_lot_size(expiry):
    """Monthly BANKNIFTY lots per NSE/FAOP/70616 for this research window.

    Expired provider records contain missing and inconsistent lot sizes, so
    they cannot safely determine historical position quantities.
    """
    if not '2025-11-01' <= expiry <= '2026-11-30':
        raise ValueError('Expiry outside verified research lot-size window')
    return 35 if expiry <= '2025-12-30' else 30

def main():
    source=json.loads((ROOT/'report.json').read_text());trades=source['trades']
    futures=json.loads((ROOT/'expired_contracts.json').read_text())+json.loads((ROOT/'current_contracts.json').read_text())
    spot={};start=pd.Timestamp('2025-11-01',tz='Asia/Kolkata');end=pd.Timestamp('2026-11-01',tz='Asia/Kolkata')
    while start<end:
        stop=min(start+pd.Timedelta(days=7),end)
        d=history(['NIDX_40000003'],start,stop,False,CACHE/('spot_'+start.strftime('%Y%m%d')+'.json'))
        for c in (d.get('data',{}).get('NIDX_40000003') or {}).get('candles') or []:spot[c['ts']]=c
        start=stop
    output=[]
    for day in sorted({t['session'] for t in trades}):
        group=[t for t in trades if t['session']==day];date=pd.Timestamp(day,tz='Asia/Kolkata')
        expiry=min(x['expiry'] for x in futures if x['expiry']>=day)
        rows=chain(expiry);expired=expiry<'2026-10-31';prepared=[]
        for t in group:
            at=pd.Timestamp(t['fill_at']);prior=spot.get(int(at.timestamp())-60)
            item={**t,'expiry':expiry,'model':'NEXT_MINUTE_OPEN_ESTIMATE'}
            if prior is None:item['coverage']='MISSING_PRE_ENTRY_SPOT';output.append(item);continue
            try:contract=select(rows,float(prior['c']),t['side'])
            except ValueError:item['coverage']='MISSING_STRIKE';output.append(item);continue
            item.update(option_symbol=contract['trading_symbol'],quantity=2*historical_lot_size(expiry),provider_lot_size=contract.get('lot_size'),lot_size_source='NSE/FAOP/70616',spot_reference=float(prior['c']),spot_reference_at=pd.Timestamp(prior['ts'],unit='s',tz='UTC').isoformat())
            identifier=contract['trading_symbol'] if expired else 'NFO_'+str(contract['security_id'])
            prepared.append((identifier,item))
        ids=sorted({i for i,_ in prepared});data={}
        for n in range(0,len(ids),50 if expired else 5):
            batch=ids[n:n+(50 if expired else 5)]
            d=history(batch,date,date+pd.Timedelta(days=1),expired,CACHE/(day+'_options_'+str(n)+'.json'));data.update(d['data'])
        for identifier,item in prepared:
            bars={c['ts']:c for c in (data.get(identifier) or {}).get('candles') or []}
            entry=int(pd.Timestamp(item['fill_at']).timestamp());exit=int(pd.Timestamp(item['exit_at']).timestamp())
            eb=bars.get(entry);xb=bars.get(exit)
            if eb and xb and eb['v']>0 and xb['v']>0:
                item['adverse_ohlc_pnl']=round((float(xb['l'])-float(eb['h']))*item['quantity'],2)
            entry_bar=bars.get(entry+60);exit_time=exit if item['result']=='TIME_EXIT' else exit+60;exit_bar=bars.get(exit_time)
            if exit_time<=entry+60:item['coverage']='SAME_MINUTE_OR_ZERO_HOLD_UNRESOLVED'
            elif not entry_bar or not exit_bar or entry_bar['v']<=0 or exit_bar['v']<=0:item['coverage']='MISSING_OR_ZERO_VOLUME_FILL_CANDLE'
            else:
                buy=float(entry_bar['o']);sell=float(exit_bar['o'])
                if buy<=0 or sell<=0:item['coverage']='INVALID_PRICE'
                else:
                    item.update(coverage='PRICED',option_entry=buy,option_exit=sell,option_entry_at=pd.Timestamp(entry+60,unit='s',tz='UTC').isoformat(),option_exit_at=pd.Timestamp(exit_time,unit='s',tz='UTC').isoformat(),gross_option_pnl=round((sell-buy)*item['quantity'],2),scenario_net_pnl=round((sell-buy-2)*item['quantity']-40,2))
            output.append(item)
        print(day,'processed',len(output),'of',len(trades),flush=True)
        (ROOT/'options_progress.json').write_text(json.dumps(output))
    output.sort(key=lambda x:x['fill_at'])
    report={'window':{'start':'2025-11-01','end_exclusive':'2026-11-01'},'underlying_signals':len(trades),'summary_gross':summary(output,'gross_option_pnl'),'scenario_summary':summary(output,'scenario_net_pnl'),'adverse_ohlc_summary':summary(output,'adverse_ohlc_pnl'),'coverage':dict(__import__('collections').Counter(r['coverage'] for r in output)),'monthly':{m:summary([r for r in output if r['session'].startswith(m)],'gross_option_pnl') for m in sorted({r['session'][:7] for r in output})},'trades':output,'assumptions':['Frozen futures signals; nearest monthly expiry; two listed strikes ITM from previous completed spot minute; two historical NSE lots per FAOP70616 (35 through Dec2025 expiry, 30 thereafter)','Option fills at next minute open after underlying event; session exit at 15:15 open; same-minute unresolved trades excluded explicitly','Scenario only: one premium point slippage per side plus Rs40 round-trip charge; NOT actual all-in statutory costs','Adverse OHLC is a separate entry-bar-high/exit-bar-low stress scenario, not executable fills','No historical bid/ask depth; candle timing, historical strike listing dates and lot-size metadata may differ from live execution','No capital-based ROI: no portfolio capital assumption; no broker orders']}
    (ROOT/'options_report.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ('trades','assumptions')},indent=2))
if __name__=='__main__':main()
