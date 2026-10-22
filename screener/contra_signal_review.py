"""Independent sparse-quote scenarios. No trading service or database writes."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from backend.paper_trend import TrendRunnerRules

IST = ZoneInfo('Asia/Kolkata')


def simulate(snapshots, at, spot, direction):
    result = {'direction': direction, 'quality': 'approximate; intragap path unknown'}
    before = [s for s in snapshots if s['received_at'] <= at]
    if not before or at-before[-1]['received_at'] > 120:
        return {**result, 'missing': 'No recent prior chain'}
    prefix = 'call' if direction == 'long' else 'put'
    rows = sorted([r for r in before[-1]['chain']['chains'] if r.get(prefix+'_token')],key=lambda r:r['strike_price'])
    if not rows:
        return {**result, 'missing': 'No contracts'}
    index = min(range(len(rows)),key=lambda i:abs(rows[i]['strike_price']-spot)) + (-2 if direction=='long' else 2)
    if not 0 <= index < len(rows):
        return {**result, 'missing': 'Contract outside chain'}
    contract=rows[index]
    if (direction=='long' and contract['strike_price']>=spot) or (direction=='short' and contract['strike_price']<=spot):
        return {**result, 'missing': 'Contract not ITM'}
    result['symbol']=contract[prefix+'_symbol']
    marks=[]
    for s in snapshots:
        if s['received_at']<at or not 0<=s.get('latency_seconds',999)<=3:
            continue
        row=next((r for r in s['chain']['chains'] if r.get(prefix+'_token')==contract[prefix+'_token']),None)
        if row and 0<row.get(prefix+'_bid',0)<=row.get(prefix+'_ask',0):
            marks.append((s['received_at'],row[prefix+'_bid'],row[prefix+'_ask']))
    if not marks or marks[0][0]-at>120:
        return {**result,'missing':'No entry quote within 120s'}
    start,bid,ask=marks[0]
    result.update(entry_delay_seconds=round(start-at,1),entry_ask=ask,spread_pass=ask-bid<=min(3,.005*ask),quantity=int(contract[prefix+'_lot_size'])*3)
    rules=TrendRunnerRules()
    trade=rules.open_trade(result['symbol'],result['quantity'],ask,direction)
    previous=start
    max_gap=0
    for stamp,bid,_ in marks:
        max_gap=max(max_gap,stamp-previous)
        previous=stamp
        rules.on_tick(trade,bid)
        local=datetime.fromtimestamp(stamp,IST)
        if trade.status=='open' and (local.hour,local.minute)>=(15,20):
            rules.close_for_market_close(trade,bid)
        if trade.status=='closed':
            result.update(pnl=round((trade.exit_price-trade.entry_price)*trade.quantity,2),exit_bid=trade.exit_price,exit_time=local.strftime('%H:%M:%S'),exit_reason=trade.exit_reason)
            break
    result['max_quote_gap_seconds']=round(max_gap,1)
    if max_gap>180:
        result['quality']='very sparse; unreliable'
    if trade.status!='closed':
        result['missing']='Exit not observed'
    return result


def main():
    day='2026-10-14'
    db=sqlite3.connect('file:backend/paper_trading.db?mode=ro',uri=True)
    events=db.execute('SELECT occurred_at,event_type,direction,ltp,details FROM paper_day_events WHERE trade_date=? ORDER BY id',(day,)).fetchall()
    db.close()
    snapshots=sorted([json.loads(e[4]) for e in events if e[1]=='oi_chain_snapshot'],key=lambda s:s['received_at'])
    results=[]
    for stamp,kind,direction,spot,_ in events:
        if kind!='signal':
            continue
        at=datetime.fromisoformat(stamp).timestamp()
        results.append({'signal_time':datetime.fromtimestamp(at,IST).strftime('%H:%M:%S'),'signal':direction,'original':simulate(snapshots,at,spot,direction),'contra':simulate(snapshots,at,spot,'short' if direction=='long' else 'long')})
    out=Path('screener/index_research_cache/contra_2026_10_14')
    out.mkdir(parents=True,exist_ok=True)
    caveats='Independent scenarios for all signals; buy CE for long, PE for short. Ask entry, bid exit, three lots, current 30-point stop and trailing rules. First available fast-response snapshot after signal, max delay 120s. Contract inferred from pre-signal chain. No costs, no daily lockouts, no overlap restrictions. Spread failures are included ONLY as hypothetical forced trades. Missing intragap prices prevent verified backtest results. Totals are arithmetic scenario sums, not executable daily P&L.'
    (out/'results.json').write_text(json.dumps({'caveats':caveats,'results':results},indent=2),encoding='utf-8')
    lines=['# October 14 original vs opposite-direction simulation','',caveats,'','| Signal IST | Original | Original sampled P&L | Contra | Contra sampled P&L | Contra delay (s) | Contra max gap (s) | Contra spread passes |','|---|---|---:|---|---:|---:|---:|---|']
    for row in results:
        a,b=row['original'],row['contra']
        lines.append(f"| {row['signal_time']} | {a.get('symbol','N/A')} | {a.get('pnl','N/A')} | {b.get('symbol','N/A')} | {b.get('pnl','N/A')} | {b.get('entry_delay_seconds','N/A')} | {b.get('max_quote_gap_seconds','N/A')} | {b.get('spread_pass','N/A')} |")
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(results,indent=2))
    for side in ('original','contra'):
        values=[r[side]['pnl'] for r in results if 'pnl' in r[side]]
        print(side,{'completed':len(values),'wins':sum(v>0 for v in values),'losses':sum(v<0 for v in values),'sum':round(sum(values),2)})


if __name__=='__main__':
    main()
