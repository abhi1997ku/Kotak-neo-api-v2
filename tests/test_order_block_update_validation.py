import pandas as pd
from dataclasses import replace
from screener.order_block_backtest_audit import replay


def test_three_confirmation_uses_current_signal_generator(monkeypatch):
    import backend.order_block_strategy as strategy
    ix=pd.date_range('2026-10-21 09:15',periods=4,freq='5min',tz='Asia/Kolkata')
    bars=pd.DataFrame({'Open':[100,98,99,100],'Close':[98,99,100,101],
                       'High':[101,100,101,102],'Low':[97,97,98,99]},index=ix)
    monkeypatch.setattr(strategy,'PARAMS',replace(strategy.PARAMS,periods=3))
    assert strategy.signals(bars,ix[-1]+pd.Timedelta(minutes=4))==[]
    found=strategy.signals(bars,ix[-1]+pd.Timedelta(minutes=5))
    assert len(found)==1 and found[0]['timestamp']=='2026-10-21T09:35:00+05:30'
    assert found[0]['entry']==98.5


def test_stop_floor_rejects_actual_gap_fill_not_only_planned_midpoint():
    ix=pd.date_range('2026-10-21 10:00',periods=2,freq='min',tz='Asia/Kolkata')
    bars=pd.DataFrame({'Open':[90.1,95],'High':[91,100],'Low':[90.1,94],'Close':[91,98]},index=ix)
    zone={'detected_at':ix[0].isoformat(),'side':'long','low':90.05,'high':110,'midpoint':100}
    unfiltered,_=replay(bars,[zone])
    filtered,_=replay(bars,[zone],min_risk=5)
    assert len(unfiltered)==1 and unfiltered[0]['risk']<1
    assert filtered==[]
