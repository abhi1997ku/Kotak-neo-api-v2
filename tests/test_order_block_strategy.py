import pandas as pd
import pytest
from backend.order_block_strategy import signals, eligible, STRATEGY
from backend.option_paper import process, execution_event


def test_confirmation_at_fifth_close_and_no_future():
    ix=pd.date_range('2026-10-21 09:15',periods=6,freq='5min',tz='Asia/Kolkata')
    b=pd.DataFrame({'Open':[100,98,99,100,101,102], 'Close':[98,99,100,101,102,103],
                    'High':[101,100,101,102,103,104], 'Low':[97,97,98,99,100,101]},index=ix)
    assert signals(b,ix[-1]+pd.Timedelta(minutes=4))==[]
    s=signals(b,ix[-1]+pd.Timedelta(minutes=5))[0]
    assert s['timestamp']=='2026-10-21T09:45:00+05:30'
    assert s['entry']==98.5 and s['stop']==96.95
    assert s['valid_until']=='2026-10-21T15:00:00+05:30'
    assert signals(b.drop(ix[2]),ix[-1]+pd.Timedelta(minutes=5))==[]


def setup(at='10:00',side='long'):
    return dict(timestamp=f'2026-10-21T{at}:00+05:30',side=side,strategy=STRATEGY,
                entry=100.,stop=90. if side=='long' else 110.,target=120.,
                valid_until='2026-10-21T15:00:00+05:30')


def state():
    return dict(position=None,trades=[],seen=[],started_at='2026-10-21T09:00:00+05:30',future_code='F')


def test_superseded_and_old_strategy_never_enter():
    st=state();old=setup('09:55');new=setup();legacy={**setup('09:50'),'strategy':'ema9_vwap_2r'}
    assert eligible(st,[old,new,legacy],pd.Timestamp('2026-10-21 10:01',tz='Asia/Kolkata'),execution_event)==[new]
    assert {e['reason'] for e in st['execution_events']}=={'SUPERSEDED_ZONE','OLD_STRATEGY_IGNORED'}


@pytest.mark.parametrize('side,waiting,fill',[('long',101,99),('short',99,101)])
def test_midpoint_retest_not_breakout_and_actual_fill_target(monkeypatch,side,waiting,fill):
    import backend.option_paper as worker
    monkeypatch.setattr(worker,'select_contract',lambda *a:dict(code='O',quantity=60,symbol='TEST'))
    monkeypatch.setattr(worker,'depth_fill',lambda *a:200.)
    now=pd.Timestamp('2026-10-21 10:01',tz='Asia/Kolkata');st=state();s=setup(side=side)
    def quote(price):
        return dict(received_at=now.isoformat(),data={'F':{'live_price':price},'NIDX_40000003':{'live_price':55000},'O':{}})
    process(st,[s],quote(waiting),[],now)
    assert st['position'] is None
    process(st,[s],quote(fill),[],now)
    p=st['position'];assert p['underlying_fill']==fill
    assert p['target']==(117 if side=='long' else 83)
