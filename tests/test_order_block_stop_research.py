import pandas as pd

from screener.order_block_stop_research import variant_replay
from screener.order_block_backtest_audit import replay


def fixture():
    ix=pd.DatetimeIndex([pd.Timestamp('2026-11-02 10:00',tz='Asia/Kolkata'),
                         pd.Timestamp('2026-11-02 10:01',tz='Asia/Kolkata'),
                         pd.Timestamp('2026-11-02 15:15',tz='Asia/Kolkata')])
    bars=pd.DataFrame({'Open':[105,110,115], 'High':[112,120,120],
                       'Low':[99,105,110], 'Close':[110,115,115]},index=ix)
    zones=[dict(detected_at=ix[0].isoformat(),side='long',midpoint=100,low=80,high=120,atr=40)]
    return bars,zones


def test_zero_buffer_matches_reference_replay():
    bars,zones=fixture()
    assert variant_replay(bars,zones,buffer_atr=0,max_risk=60)==replay(bars,zones,min_risk=15,max_risk=60)[0]


def test_rejection_fills_only_next_bar_open():
    bars,zones=fixture()
    trade=variant_replay(bars,zones,rejection=True)[0]
    assert trade['fill_at']==bars.index[1].isoformat()
    assert trade['fill']==110
    assert trade['stop']==70
    assert trade['risk']==40


def test_missing_next_minute_does_not_backfill_rejection():
    bars,zones=fixture()
    assert variant_replay(bars.drop(bars.index[1]),zones,rejection=True)==[]
