"""Independent audit of the three-confirmation research result."""
import json
from pathlib import Path
from collections import Counter
import pandas as pd
from backend.indstocks_monitor import completed_bars
from screener.order_block_variant_comparison import zones_for, stats


def replay(one, zones, min_risk=0, max_risk=float("inf")):
    pending={};position=None;out=[];ambiguities=[]
    for at,b in one.iterrows():
        if position is None:
            for z in zones:
                if pd.Timestamp(z['detected_at'])==at:pending[z['side']]=z
        opened=False
        if position is None and '09:30'<=at.strftime('%H:%M')<'15:00':
            for side,z in list(pending.items()):
                direction=1 if side=='long' else -1
                stop=z['low']-.05 if side=='long' else z['high']+.05
                if direction*(b.Open-stop)<=0:
                    del pending[side];continue
                if (side=='long' and b.Low>z['midpoint']) or (side=='short' and b.High<z['midpoint']):continue
                opened=direction*(b.Open-z['midpoint'])<=0
                fill=float(b.Open if opened else z['midpoint'])
                risk=direction*(fill-stop)
                if risk < min_risk or risk > max_risk:
                    del pending[side]
                    continue
                position=dict(fill_at=at.isoformat(),side=side,fill=fill,stop=stop,target=fill+direction*2*risk,risk=risk)
                pending={};break
        if position is None:continue
        p=position;direction=1 if p['side']=='long' else -1
        stop_hit=b.Low<=p['stop'] if direction==1 else b.High>=p['stop']
        target_hit=b.High>=p['target'] if direction==1 else b.Low<=p['target']
        if stop_hit and target_hit:ambiguities.append(at.isoformat())
        if at.strftime('%H:%M')>='15:15':price=float(b.Open);reason='TIME_EXIT'
        elif stop_hit:price=float(min(b.Open,p['stop']) if direction==1 else max(b.Open,p['stop']));reason='STOP'
        elif target_hit and (p['fill_at']!=at.isoformat() or opened):price=p['target'];reason='TARGET'
        else:continue
        points=direction*(price-p['fill'])
        out.append({**p,'exit_at':at.isoformat(),'exit_price':price,'result':reason,'points':round(points,4),'r':round(points/p['risk'],4)})
        position=None
    assert position is None
    return out,ambiguities


def main():
    report=json.loads(Path('backend/data/order_block_variant_comparison.json').read_text())
    original=report['variants']['5m_3_midpoint']['trades']
    sessions={}
    for path in Path('backend/data/indstocks_history').glob('*.json'):
        for code,payload in json.loads(path.read_text()).get('data',{}).items():
            cs=payload.get('candles',[])
            if not cs:continue
            end=pd.Timestamp(max(c['ts'] for c in cs),unit='s',tz='UTC')+pd.Timedelta(minutes=1)
            one,_=completed_bars(cs,end)
            for day,g in one.groupby(one.index.date):
                if str(day) in report['sessions']:
                    assert (g.High>=g[['Open','Close','Low']].max(axis=1)).all()
                    assert (g.Low<=g[['Open','Close','High']].min(axis=1)).all()
                    assert len(g)==375 and g.index.is_unique
                    sessions[str(day)]=g
    all_trades=[];ambiguous=[];prefix_checks=0
    for day,one in sorted(sessions.items()):
        zones=zones_for(one,5,3,False)
        # Rebuild signal definitions independently from origin + three CLOSED candles.
        five=one.resample('5min').agg(dict(Open='first',High='max',Low='min',Close='last',Volume='sum'))
        expected=[]
        for i in range(3,len(five)):
            origin=five.iloc[i-3];following=five.iloc[i-2:i+1]
            side='long' if origin.Close<origin.Open and (following.Close>following.Open).all() else 'short' if origin.Close>origin.Open and (following.Close<following.Open).all() else None
            at=five.index[i]+pd.Timedelta(minutes=5)
            if side and at.strftime('%H:%M')<'15:00':expected.append((at.isoformat(),side))
        assert expected==[(z['detected_at'],z['side']) for z in zones]
        for n in range(5,376,5):
            cutoff=one.index[0]+pd.Timedelta(minutes=n)
            assert zones_for(one.iloc[:n],5,3,False)==[z for z in zones if pd.Timestamp(z['detected_at'])<=cutoff]
            prefix_checks+=1
        result,amb=replay(one,zones);ambiguous+=amb
        reference=[t for t in original if t['session']==day]
        fields=['fill_at','side','fill','stop','target','exit_at','exit_price','result','points','r']
        assert [{k:t[k] for k in fields} for t in result]==[{k:t[k] for k in fields} for t in reference],day
        all_trades+=result
    reasons=Counter(t['result'] for t in all_trades)
    summary=stats(all_trades,len(sessions))
    costs=[]
    for cost in [1,2,5]:
        costs.append({'hypothetical_round_trip_futures_points':cost,'net_points':round(sum(t['points']-cost for t in all_trades),2),'net_r':round(sum((t['points']-cost)/t['risk'] for t in all_trades),3)})
    audit={'summary':summary,'prefix_checks':prefix_checks,'all_49_trades_match_independent_replay':True,'exit_counts':dict(reasons),'ambiguous_stop_target_bars':ambiguous,'risk_min':min(t['risk'] for t in all_trades),'risk_max':max(t['risk'] for t in all_trades),'time_exits':[t for t in all_trades if t['result']=='TIME_EXIT'],'cost_sensitivity_not_actual_option_costs':costs,'drawdown_definition':'Closed-trade cumulative R; not intratrade drawdown or option rupee drawdown'}
    Path('backend/data/order_block_3_confirmation_audit.json').write_text(json.dumps(audit,indent=2))
    print(json.dumps(audit,indent=2))

if __name__=='__main__':main()
