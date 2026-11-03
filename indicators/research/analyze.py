"""Test hypotheses using rendered original chart-table values."""
import json
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).parent
data=json.loads((ROOT/'call_observations.json').read_text(encoding='utf-8-sig'))
rows=sorted(data['rows'],key=lambda r:float(r[7].replace(',','')))
def num(v): return None if v=='∅' else float(v.replace(',',''))
bars=[{k:v if k in ['date','change'] else num(v) for k,v in zip(data['columns'],r)} for r in rows]
checks={}
for source in ['open','high','low','close','hl2','hlc3','ohlc4']:
    errors=[]
    for p,b in zip(bars,bars[1:]):
        if b['bar_time']-p['bar_time']!=60: continue
        v=b.get(source)
        if source=='hl2': v=(b['high']+b['low'])/2
        if source=='hlc3': v=(b['high']+b['low']+b['close'])/3
        if source=='ohlc4': v=sum(b[k] for k in ['open','high','low','close'])/4
        errors.append(abs(b['ribbon']-(p['ribbon']*5/6+v/6)))
    checks[source]={'n':len(errors),'within_0.011':sum(e<=.011 for e in errors),'mae':sum(errors)/len(errors),'max':max(errors)}
trials=[]
for length in [11,21,34,50,89,100,200]:
    avg=bars[0]['close']; correct=0
    for i,b in enumerate(bars):
        avg+=2/(length+1)*(b['close']-avg)
        if i>=200: correct+=int((2 if b['close']>avg else 0)==b['state'])
    trials.append({'rule':f'close vs EMA({length})','correct':correct,'n':len(bars)-200})
transitions=[]; bases=[]
for i,b in enumerate(bars):
    if i and (b['state']!=bars[i-1]['state'] or b['buy'] or b['sell']):
        transitions.append({k:b[k] for k in ['date','close','ribbon','state','buy','sell']})
    if i==0 or b['base']!=bars[i-1]['base']:
        bases.append({k:b[k] for k in ['date','open','high','low','close','base']})
report={'bars':len(bars),'start':bars[0]['date'],'end':bars[-1]['date'],'states':dict(Counter(b['state'] for b in bars)),'ema_checks':checks,'simple_rule_checks':trials,'transitions':transitions,'base_changes':bases}
(ROOT/'analysis.json').write_text(json.dumps(report,indent=2))
if __name__ == '__main__':
    print(json.dumps(report,indent=2))
