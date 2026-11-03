"""Compare repeated rendered table observations without inferring execution timing."""
import json
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).parent
payload=json.loads((ROOT/'live_2026-11-02_10s.json').read_text(encoding='utf-8'))
samples=payload['samples']
history=defaultdict(list)
def num(x):
    return None if x=='\u2205' else float(x.replace(',',''))
for sample in samples:
    rows=[r for r in sample['rows'] if len(r)==18]
    if not rows: continue
    latest=max(num(r[7]) for r in rows)
    for r in rows:
        history[num(r[7])].append({'observed':sample['observedUtc'],'row':r,'closed_in_snapshot':num(r[7])<latest})

changing=[]; signal_revisions=[]; closed_revisions=[]
for epoch,observations in sorted(history.items()):
    if len({o['row'][4] for o in observations})>1: changing.append(epoch)
    signals={(o['row'][14],o['row'][15],o['row'][16]) for o in observations}
    if len(signals)>1: signal_revisions.append(observations)
    settled=[o for o in observations if o['closed_in_snapshot']]
    if len({tuple(o['row'][i] for i in [1,2,3,4,9,13,14,15,16]) for o in settled})>1:
        closed_revisions.append(settled)

final={e:os[-1]['row'] for e,os in history.items()}
errs=[]
for e,r in sorted(final.items()):
    if e-10 in final:
        predicted=(5*num(final[e-10][13])+num(r[4]))/6
        errs.append(abs(num(r[13])-predicted))
live_latest={num(s['rows'][2][7]) for s in samples if len(s['rows'])>2 and len(s['rows'][2])==18}
report={
    'symbol':samples[-1]['rows'][0][1], 'timeframe':'10 seconds',
    'samples':len(samples),'observed_utc_start':samples[0]['observedUtc'],'observed_utc_end':samples[-1]['observedUtc'],
    'latest_bar_first':samples[0]['rows'][2][0],'latest_bar_last':samples[-1]['rows'][2][0],
    'distinct_bars_including_initial_history':len(history),'distinct_live_latest_bars':len(live_latest),
    'bars_with_observed_close_changes':len(changing),
    'observed_states':sorted({r[16] for r in final.values()}),
    'signal_bars':[r for r in final.values() if num(r[14]) or num(r[15])],
    'state_or_signal_revisions':signal_revisions,'closed_bar_revisions':closed_revisions,
    'ema11_recurrence':{'comparisons':len(errs),'within_0_011':sum(e<=.011 for e in errs),'max_error':max(errs) if errs else None},
    'limits':'Polling can miss changes between samples. Later timestamp in the same snapshot is used as the closed-bar proxy; no exchange-finality claim. No signal transition means signal repainting and confirmation timing remain undetermined.'
}
(ROOT/'live_2026-11-02_analysis.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
