"""Bounded, chronological rule search on chart observations. No trade execution.

Fit only Nov 3 10s before 12:30; freeze the winner before evaluating afternoon
and other timeframes. Raw files remain untouched. Features never use labels.
"""
import json
import math
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).parent
IST=timezone(timedelta(hours=5,minutes=30))
CUTOFF=datetime(2026,11,3,12,30,tzinfo=IST).timestamp()
SESSION=datetime(2026,11,3,9,15,tzinfo=IST).timestamp()
FIELDS={'o':1,'h':2,'l':3,'c':4,'t':7,'base':9,'period':12,'ribbon':13,'buy':14,'sell':15,'state':16}


def number(s):
    return None if s=='\u2205' else float(s.replace(',',''))


def load_day(path):
    latest={};closed={}
    for line in path.open(encoding='utf8'):
        p=json.loads(line)
        rows=[r for r in p['rows'] if len(r)==18]
        newest=max((number(r[7]) for r in rows),default=0)
        for r in rows:
            t=number(r[7]);latest[t]=r
            if t<newest:closed[t]=r
    # Use only completed-proxy rows, preserving preceding-day history as warm-up.
    return [{k:number(r[i]) for k,i in FIELDS.items()} for t,r in sorted(closed.items())]


def ema(values,n):
    out=[]
    for v in values:out.append(v if not out else out[-1]+2/(n+1)*(v-out[-1]))
    return out


def rma(values,n):
    out=[]
    for v in values:out.append(v if not out else out[-1]+(v-out[-1])/n)
    return out


def features(b):
    c=[x['c'] for x in b]
    tr=[max(x['h']-x['l'],abs(x['h']-b[max(0,i-1)]['c']),abs(x['l']-b[max(0,i-1)]['c'])) for i,x in enumerate(b)]
    f={'close':c,'ribbon':[x['ribbon'] for x in b]}
    for n in [5,9,11,14,20,21,34,50,55,89,100,144,200]:f[f'ema{n}']=ema(c,n)
    for n in [7,10,14,21]:
        f[f'atr{n}']=rma(tr,n)
        changes=[x-b[max(0,i-1)]['c'] for i,x in enumerate(c)]
        up=rma([max(x,0) for x in changes],n);dn=rma([max(-x,0) for x in changes],n)
        f[f'rsi{n}']=[100*u/(u+d) if u+d else 50 for u,d in zip(up,dn)]
    for n in [5,9,11,14,20,21,34,50,100]:
        f[f'high{n}']=ema([x['h'] for x in b],n)
        f[f'low{n}']=ema([x['l'] for x in b],n)
    return f


def supertrend(b,atr,mult):
    dirs=[];upper=lower=0;direction=0
    for i,x in enumerate(b):
        midpoint=(x['h']+x['l'])/2
        u=midpoint+mult*atr[i];l=midpoint-mult*atr[i]
        if i:
            u=u if u<upper or b[i-1]['c']>upper else upper
            l=l if l>lower or b[i-1]['c']<lower else lower
            direction=(1 if x['c']>u else 0) if direction==0 else (0 if x['c']<l else 1)
        upper,lower=u,l;dirs.append(direction)
    return dirs


def predict(spec,b,f):
    family=spec['family'];pred=[]
    if family=='ema_stack':
        fast=f[f"ema{spec['fast']}"];slow=f[f"ema{spec['slow']}"]
        for i,x in enumerate(b):
            if spec['mode']=='price_fast':v=0 if x['c']<slow[i] else 2 if fast[i]>slow[i] else 1
            elif spec['mode']=='stack_price':v=2 if fast[i]>slow[i] else 1 if x['c']>fast[i] else 0
            else:v=2 if fast[i]>slow[i] else 1 if fast[i]>fast[max(i-1,0)] else 0
            pred.append(v)
    elif family=='supertrend':
        trend=supertrend(b,f[f"atr{spec['atr']}"],spec['mult'])
        for i,x in enumerate(b):
            recover=x['c']>f[f"ema{spec['ema']}"][i] if spec['recovery']=='ema' else f[f"rsi{spec['rsi']}"][i]>spec['threshold']
            pred.append(2 if trend[i] else 1 if recover else 0)
    elif family=='rsi_latch':
        state=0
        for value in f[f"rsi{spec['rsi']}"]:
            if value<spec['bear']:state=0
            elif value>spec['bull']:state=2
            elif state==0 and value>spec['recover']:state=1
            pred.append(state)
    elif family=='breakout_latch':
        state=0
        for i,x in enumerate(b):
            prev=b[max(0,i-spec['lookback']):i]
            if prev:
                if x['c']<min(y['l'] for y in prev):state=0
                elif x['c']>max(y['h'] for y in prev):state=2
                elif state==0 and x['c']>f[f"ema{spec['ema']}"][i]:state=1
            pred.append(state)
    elif family=='channel':
        upper=f[f"high{spec['length']}"];lower=f[f"low{spec['length']}"]
        state=0
        for i in range(len(b)):
            j=max(0,i-spec['shift']);price=b[j]['c']
            if price<lower[j]:state=0
            elif price>upper[j]:state=2
            elif state==0 or not spec['latch']:state=1
            pred.append(state)
    elif family=='channel_confirm':
        upper=f[f"high{spec['length']}"];lower=f[f"low{spec['length']}"]
        state=0;count=0
        for i in range(len(b)):
            j=max(0,i-spec['shift']);price=b[j]['c']
            if price<lower[j]:state=0;count=0
            elif price>upper[j]:
                count+=1
                state=2 if count>=spec['confirm'] else 1
            elif state!=2:count=0;state=0 if spec['inside']=='bear' else 1
            pred.append(state)
    return pred


def score(pred,b,indices):
    matrix=[[0]*3 for _ in range(3)]
    for i in indices:matrix[int(b[i]['state'])][pred[i]]+=1
    total=sum(map(sum,matrix));correct=sum(matrix[i][i] for i in range(3))
    f1=[]
    for k in range(3):
        tp=matrix[k][k];actual=sum(matrix[k]);estimated=sum(r[k] for r in matrix)
        if actual:f1.append(2*tp/(actual+estimated) if actual+estimated else 0)
    return {'n':total,'accuracy':correct/total if total else None,'macro_f1':sum(f1)/len(f1) if f1 else None,'confusion_actual_rows':matrix}


def candidates():
    for fast in [5,9,11,20,21,34,50]:
        for slow in [21,34,50,55,89,100,144,200]:
            if fast>=slow:continue
            for mode in ['price_fast','stack_price','stack_slope']:yield {'family':'ema_stack','fast':fast,'slow':slow,'mode':mode}
    for atr in [7,10,14,21]:
        for mult in [1,1.5,2,2.5,3,3.5,4]:
            for n in [11,21,50]:yield {'family':'supertrend','atr':atr,'mult':mult,'recovery':'ema','ema':n}
            for rsi in [7,14]:
                for threshold in [45,50,55]:yield {'family':'supertrend','atr':atr,'mult':mult,'recovery':'rsi','rsi':rsi,'threshold':threshold}
    for rsi in [7,10,14,21]:
        for bear in [30,35,40,45,50]:
            for bull in [55,60,65,70]:
                for recover in [45,50,55]:
                    if bear<recover<bull:yield {'family':'rsi_latch','rsi':rsi,'bear':bear,'bull':bull,'recover':recover}
    for n in [5,10,14,20,34,50]:
        for ema_length in [11,21,50]:yield {'family':'breakout_latch','lookback':n,'ema':ema_length}
    for n in [5,9,11,14,20,21,34,50,100]:
        for shift in [0,1]:
            for latch in [False,True]:yield {'family':'channel','length':n,'shift':shift,'latch':latch}
            for confirm in [2,3,4]:
                for inside in ['bear','recovery']:yield {'family':'channel_confirm','length':n,'shift':shift,'confirm':confirm,'inside':inside}


def signal_score(pred,b,indices):
    result={}
    for name,target,col in [('BUY',2,'buy'),('SELL',0,'sell')]:
        tp=fp=fn=0
        for i in indices:
            if not i or b[i]['t']-b[i-1]['t']!=b[i]['period']:continue
            guess=pred[i]==target and pred[i-1]!=target
            actual=b[i][col]==1
            tp+=guess and actual;fp+=guess and not actual;fn+=actual and not guess
        result[name]={'exact_bar_matches':tp,'extra_signals':fp,'missed_signals':fn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None}
    return result


def main():
    paths={tf:ROOT/'sessions/2026-11-03'/f'primary_{tf}.jsonl' for tf in ['10s','1m','5m']}
    cache=ROOT/'rule_search_bars.json'
    manifest_path=ROOT/'rule_search_cache_manifest.json'
    fingerprints={tf:hashlib.file_digest(path.open('rb'),'sha256').hexdigest() for tf,path in paths.items()}
    cache_valid=cache.exists() and manifest_path.exists() and json.loads(manifest_path.read_text())==fingerprints
    datasets=json.loads(cache.read_text()) if cache_valid else {tf:load_day(path) for tf,path in paths.items()}
    b=datasets['10s'];f=features(b)
    train=[i for i,x in enumerate(b) if SESSION<=x['t']<CUTOFF]
    leaderboard=[]
    for spec in candidates():
        pred=predict(spec,b,f);metrics=score(pred,b,train)
        leaderboard.append({'spec':spec,'train':metrics})
    leaderboard.sort(key=lambda x:(x['train']['macro_f1'],x['train']['accuracy']),reverse=True)
    winner=leaderboard[0]['spec']
    report={'method':'Select by state macro-F1 on Nov 3 10s 09:15-12:30 only. Freeze winner; evaluate afternoon and other timeframes. Labels are never features. Full preceding captured history used as warm-up; initialization uncertainty remains, especially 5m. Signal predictions use entry into state only, an explicit hypothesis.',
            'input_sha256':fingerprints,'selection_caveat':'Candidate families were expanded after viewing the initial same-day result. Treat same-day scores as exploratory validation; Nov 2 external evaluation was run only after the final candidate was selected.',
            'candidate_count':len(leaderboard),'top_training_candidates':leaderboard[:10],'frozen_winner':winner,'evaluation':{}}
    for tf,b in datasets.items():
        pred=predict(winner,b,features(b))
        indices=[i for i,x in enumerate(b) if x['t']>=CUTOFF]
        report['evaluation'][tf]={'state':score(pred,b,indices),'signals':signal_score(pred,b,indices)}
    external=load_day(ROOT/'day_2026-11-02.jsonl')
    external_pred=predict(winner,external,features(external))
    # A missing interval makes recursive feature initialization uncertain.
    # Exclude at least 3 channel lengths after startup or each observed gap.
    indices=[];run=0
    for i,x in enumerate(external):
        run=run+1 if i and x['t']-external[i-1]['t']==x['period'] else 1
        if run>3*winner.get('length',200):indices.append(i)
    report['external_nov2_10s']={'warmup_policy':'Exclude first 3 selected channel lengths after startup and each gap; no parameter retuning.',
        'state':score(external_pred,external,indices),'signals':signal_score(external_pred,external,indices)}
    (ROOT/'rule_search_results.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    # Compact completed-proxy datasets make the fit reproducible without reparsing 265MB.
    # Older caches used Python JSON NaN for missing markers; normalize on write.
    clean={tf:[{k:None if isinstance(v,float) and math.isnan(v) else v for k,v in x.items()} for x in bars] for tf,bars in datasets.items()}
    (ROOT/'rule_search_bars.json').write_text(json.dumps(clean,separators=(',',':'),allow_nan=False),encoding='utf8')
    manifest_path.write_text(json.dumps(fingerprints,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='top_training_candidates'},indent=2))


if __name__=='__main__':main()
