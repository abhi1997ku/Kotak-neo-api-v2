import pandas as pd
from backend.indstocks_monitor import completed_bars


def test_partial_bars_and_missing_context_minutes_excluded():
    start = pd.Timestamp('2026-10-16 09:15', tz='Asia/Kolkata')
    candles = [dict(ts=int((start+pd.Timedelta(minutes=i)).timestamp()), o=100,h=101,l=99,c=100,v=10) for i in range(11)]
    one, five = completed_bars(candles, start+pd.Timedelta(minutes=10,seconds=20))
    assert len(one)==10 and len(five)==2
    one, five = completed_bars(candles[1:], start+pd.Timedelta(minutes=10,seconds=20))
    assert len(five)==1
    assert five.index[0]==start+pd.Timedelta(minutes=5)


def test_observer_uses_two_r_and_separate_records(monkeypatch, tmp_path):
    import json
    import sqlite3
    import backend.indstocks_monitor as monitor
    import screener.ema_vwap_research as engine
    now = pd.Timestamp('2026-10-19 10:00', tz='Asia/Kolkata')
    monkeypatch.setattr(monitor, 'resolve_future', lambda _: {'SECURITY_ID': '1', 'TRADING_SYMBOL': 'TEST'})
    monkeypatch.setattr(monitor.client, 'history', lambda *args: {'data': {'NFO_1': {'candles': []}}})
    frame = pd.DataFrame({'Close': [100]}, index=[now-pd.Timedelta(minutes=1)])
    monkeypatch.setattr(monitor, 'completed_bars', lambda *args: (frame, frame))
    def fake_signals(one, at, config, diagnostics=None):
        assert config.target_r == 2.0
        diagnostics.append({'timestamp': now.isoformat(), 'side': 'short', 'accepted': False,
                            'reason_codes': ['NO_EMA_TOUCH']})
        return [{'timestamp': now.isoformat(), 'side': 'long', 'entry': 100, 'stop': 95, 'target': 110}]
    monkeypatch.setattr(engine, 'signals', fake_signals)
    path = tmp_path/'observations.sqlite'
    result = monitor.observe(path, now)
    assert result['strategy'] == 'ema9_vwap_2r'
    assert not result['orders_enabled'] and not result['fills_enabled']
    monitor.observe(path, now)
    with sqlite3.connect(path) as db:
        rows = db.execute('SELECT payload FROM observations').fetchall()
    assert len(rows) == 2
    assert json.loads(rows[0][0])['target_price'] == 110
    assert result['rejected'] == 1
    assert json.loads(rows[1][0])['reason_codes'] == ['NO_EMA_TOUCH']
