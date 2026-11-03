"""Audit only the selected session; distinguish captured bars from closure proxies."""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).parent
IST=timezone(timedelta(hours=5,minutes=30))
DAY='2026-11-03'
folder=ROOT/'sessions'/DAY
reports={}
for name,step in [('primary_10s',10),('primary_1m',60),('primary_5m',300)]:
    observations=defaultdict(list)
    for line in (folder/(name+'.jsonl')).open(encoding='utf8'):
        packet=json.loads(line)
        rows=[r for r in packet['rows'] if len(r)==18]
        newest=max((float(r[7].replace(',','')) for r in rows),default=0)
        for r in rows:
            e=float(r[7].replace(',',''))
            t=datetime.fromtimestamp(e,IST)
            if t.date().isoformat()==DAY and '09:15:00'<=t.strftime('%H:%M:%S')<'15:30:00':
                observations[e].append((r,e<newest))
    epochs=sorted(observations)
    final={e:observations[e][-1][0] for e in epochs}
    closed={e:next((r for r,c in reversed(observations[e]) if c),None) for e in epochs}
    errors=[]
    for a,b in zip(epochs,epochs[1:]):
        if b-a==step and closed[a] and closed[b]:
            errors.append(abs(float(closed[b][13].replace(',', ''))-(5*float(closed[a][13].replace(',', ''))+float(closed[b][4].replace(',', '')))/6))
    vanished=[]
    for e,versions in observations.items():
        for col in [14,15]:
            if closed[e] and any(r[col]=='1.00' for r,c in versions) and closed[e][col]!='1.00':
                vanished.append({'bar':final[e][0],'marker':'BUY' if col==14 else 'SELL'})
    tests=[]
    bars=[closed[e] for e in epochs if closed[e]]
    for length in [11,21,50,100,200]:
        avg=float(bars[0][4].replace(',', ''));correct=total=0
        for i,r in enumerate(bars):
            price=float(r[4].replace(',', ''));avg+=2/(length+1)*(price-avg)
            if i>=min(200,len(bars)//2):
                state=2 if price>avg else 0
                correct+=state==float(r[16]);total+=1
        tests.append({'rule':f'close above/below EMA({length})','correct':correct,'total':total,'accuracy':round(correct/total,4) if total else None})
    signals=[r for r in bars if r[14]=='1.00' or r[15]=='1.00']
    reports[name]={'captured_session_bars':len(epochs),'expected_session_bars':22500//step,
       'coverage_percent':round(100*len(epochs)/(22500//step),2),'closed_proxy_bars':len(bars),
       'first_bar':final[epochs[0]][0],'last_bar':final[epochs[-1]][0],
       'buys_closed_proxy':sum(r[14]=='1.00' for r in signals),'sells_closed_proxy':sum(r[15]=='1.00' for r in signals),
       'ema11_comparisons':len(errors),'ema11_matches':sum(e<=.011 for e in errors),
       'ema11_max_error':max(errors) if errors else None,'vanished_markers':vanished,'baseline_rule_tests':tests,
       'ttc_bases':sorted({r[9] for r in final.values()},key=lambda s:float(s.replace(',','')))}
out=folder/'session_audit.json'
out.write_text(json.dumps(reports,indent=2),encoding='utf8')
print(json.dumps(reports,indent=2))

