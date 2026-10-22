import pytest
from screener.paper_stop_comparison import structure_trace


def bars():
    return [dict(minute=29862945+i,open=100-i,high=102-i,low=98-i,close=100-i,volume=100) for i in range(20)]


def test_gap_through_stop_uses_open_not_stop():
    data=bars()
    data[5].update(open=110,high=111,low=109,close=110)
    result=structure_trace(data,3,"short")
    assert result['exit_proxy']==110
    assert result['directional_futures_points']==-13


def test_missing_minutes_reject_replay():
    data=bars(); del data[5]
    with pytest.raises(ValueError): structure_trace(data,3,"short")


def test_no_future_candle_can_change_an_already_triggered_exit():
    data=bars()
    data[5].update(open=105,high=110,low=95,close=100)
    result=structure_trace(data,3,"short")
    for b in data[6:]: b.update(open=1,high=1000,low=1,close=1)
    assert structure_trace(data,3,"short")==result


def test_long_and_short_mirror_without_assuming_option_pnl():
    data=bars()
    data[6].update(open=105,high=110,low=95,close=100)
    mirror=[{**b,"open":200-b['open'],"high":200-b['low'],"low":200-b['high'],"close":200-b['close']} for b in data]
    short=structure_trace(data,3,"short")
    long=structure_trace(mirror,3,"long")
    assert short['directional_futures_points']==long['directional_futures_points']
    assert short['option_pnl'] is None and long['option_pnl'] is None
    assert not short['eligible_for_adoption']
