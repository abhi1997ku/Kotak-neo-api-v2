import pandas as pd
from screener.pine_ob_research import detect


def test_detection_delay_and_wick_bounds():
    index=pd.date_range('2026-10-21 09:15',periods=7,freq='min',tz='Asia/Kolkata')
    bars=pd.DataFrame({'Open':[100,98,99,100,101,102,103],
                       'Close':[98,99,100,101,102,103,104],
                       'High':[101,100,101,102,103,104,105],
                       'Low':[97,97,98,99,100,101,102]},index=index)
    assert detect(bars.iloc[:6]) == []
    zone=detect(bars)[0]
    assert zone['origin_at']==index[0].isoformat()
    assert zone['detected_at']==index[6].isoformat()
    assert zone['high']==100 and zone['low']==97
    assert detect(bars,usewicks=True)[0]['high']==101
    assert detect(bars,threshold=10)==[]
    mirror=bars.copy()
    mirror['Open']=200-bars.Open;mirror['Close']=200-bars.Close
    mirror['High']=200-bars.Low;mirror['Low']=200-bars.High
    assert detect(mirror)[0]['side']=='short'


def test_simulation_waits_for_detection_and_stop_first():
    from screener.pine_ob_research import simulate
    index=pd.date_range('2026-10-21 09:30',periods=3,freq='min',tz='Asia/Kolkata')
    bars=pd.DataFrame({'Open':[100,100,100],'High':[110,110,110],'Low':[90,90,90],'Close':[100,100,100]},index=index)
    zone={'origin_at':index[0].isoformat(),'detected_at':index[1].isoformat(),'side':'long','high':102.,'low':98.,'midpoint':100.}
    trades=simulate(bars,[zone])
    assert len(trades)==1
    assert trades[0]['fill_at']==index[1].isoformat()
    assert trades[0]['result']=='STOP'
    assert trades[0]['r']==-1
