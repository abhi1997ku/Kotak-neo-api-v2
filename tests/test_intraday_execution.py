import pandas as pd
import pytest
from screener.intraday_execution import new_account, queue, process, Risk


def signal(side='long'):
    return dict(symbol='TEST', side=side, timestamp='2026-11-03T10:00:00+05:30',
                valid_until='2026-11-03T10:03:00+05:30', entry=100., stop=99. if side=='long' else 101.,
                target=105. if side=='long' else 95., reference_volume=100000)


def bar(o=100., h=100.5, l=99.5, c=100.):
    return dict(Open=o, High=h, Low=l, Close=c, Volume=100000)


def test_next_full_minute_idempotence_and_size():
    a=new_account(); s=signal()
    queue(a,s,'2026-11-03T10:00:02+05:30'); queue(a,s,'2026-11-03T10:00:03+05:30')
    process(a,{'TEST':bar()},'2026-11-03T10:00:00+05:30')
    assert not a['trades']
    process(a,{'TEST':bar()},'2026-11-03T10:01:00+05:30')
    t=a['trades'][0]
    assert t['quantity']*(t['entry']-99)+t['entry_fee'] < 250
    process(a,{'TEST':bar()},'2026-11-03T10:01:00+05:30')
    assert len(a['trades'])==len(a['orders'])==1


@pytest.mark.parametrize('side',['long','short'])
def test_gap_stop_and_costs(side):
    a=new_account(); queue(a,signal(side),'2026-11-03T10:00:00+05:30')
    process(a,{'TEST':bar()},'2026-11-03T10:00:00+05:30')
    gap=98 if side=='long' else 102
    process(a,{'TEST':bar(gap,gap+.1,gap-.1,gap)},'2026-11-03T10:01:00+05:30')
    assert a['trades'][0]['exit_reason']=='GAP_STOP'
    assert a['trades'][0]['net_pnl']<0


def test_expiry_and_missing_data():
    a=new_account(); queue(a,signal(),'2026-11-03T10:04:00+05:30')
    assert a['orders'][0]['status']=='EXPIRED'
    a=new_account(); queue(a,signal(),'2026-11-03T10:00:00+05:30')
    process(a,{'TEST':bar()},'2026-11-03T10:00:00+05:30')
    with pytest.raises(ValueError): process(a,{},'2026-11-03T10:01:00+05:30')


def test_stop_first_ambiguity():
    a=new_account(); queue(a,signal(),'2026-11-03T10:00:00+05:30')
    process(a,{'TEST':bar(100,106,98,100)},'2026-11-03T10:00:00+05:30')
    assert a['trades'][0]['exit_reason']=='SAME_BAR_STOP'


def test_time_exit_and_daily_halt():
    a=new_account(); queue(a,signal(),'2026-11-03T10:00:00+05:30')
    process(a,{'TEST':bar()},'2026-11-03T10:00:00+05:30')
    a['last_bar']='2026-11-03T15:14:00+05:30'
    process(a,{'TEST':bar()},'2026-11-03T15:15:00+05:30')
    assert a['trades'][0]['exit_reason']=='TIME_EXIT'
    a=new_account(); a.update(day='2026-11-03',balance=98900)
    queue(a,signal(),'2026-11-03T10:00:00+05:30')
    process(a,{'TEST':bar()},'2026-11-03T10:00:00+05:30')
    assert a['halted'] and not a['trades']


def test_position_limit_and_daily_entry_limit():
    a=new_account()
    for symbol in ('A','B','C'):
        queue(a,dict(signal(),symbol=symbol),'2026-11-03T10:00:00+05:30')
    process(a,{s:bar() for s in ('A','B','C')},'2026-11-03T10:00:00+05:30')
    assert len(a['trades']) <= 2
    assert a['orders'][-1]['status']=='SKIPPED'
    assert sum(t['quantity']*t['entry'] for t in a['trades']) <= 90000
    a=new_account()
    for i in range(4):
        stamp=f'2026-11-03T10:0{i}:00+05:30'
        s=dict(signal(),symbol=str(i),timestamp=stamp,valid_until=f'2026-11-03T10:0{i+1}:00+05:30')
        queue(a,s,stamp)
        process(a,{str(i):bar(100,100.5,98,100)},stamp)
    assert len(a['trades'])==3
    assert a['orders'][-1]['reason']=='PORTFOLIO_LIMIT'
