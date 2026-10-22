import numpy as np
import pandas as pd
import pytest
from screener.smooth_trailing import execution_features, minute_bars, ema_stop, manage_minute
from screener.trailing_swing import make_plan, open_trade


def candles(n=200):
    index = pd.date_range('2026-10-09 09:15', periods=n, freq='min', tz='Asia/Kolkata')
    p = np.arange(n, dtype=float)+20000
    return pd.DataFrame(dict(Open=p, High=p+2, Low=p-2, Close=p+1), index=index)


def position(d=1):
    row = pd.Series(dict(direction=d, width=40., upper=1000., lower=1000.))
    ts = pd.Timestamp('2026-10-09 10:15', tz='Asia/Kolkata')
    plan = make_plan('NIFTY', row, ts)
    return open_trade(plan, plan['entry_trigger'], ts)


def test_complete_candles_only():
    raw = candles()
    assert len(minute_bars(raw, now=raw.index[99])) == 99
    assert len(execution_features(raw, 5)) == 40
    missing = execution_features(raw.drop(raw.index[6]), 5)
    assert len(missing) == 39 and raw.index[5] not in missing.index


def test_saved_utc_and_provider_local_candles_merge():
    raw = candles()
    saved = minute_bars(raw.iloc[:100])
    fresh = minute_bars(raw.iloc[90:])
    assert len(minute_bars(pd.concat([saved, fresh]))) == len(raw)


def test_ema_causality_and_gap_warmup():
    raw = candles()
    np.testing.assert_allclose(execution_features(raw).ema.iloc[:100], execution_features(raw.iloc[:100]).ema)
    broken = execution_features(raw.drop(raw.index[100]))
    assert broken.loc[raw.index[101], 'gap']
    assert not broken.loc[raw.index[129], 'ready']
    assert broken.loc[raw.index[130], 'ready']


@pytest.mark.parametrize('direction', [1, -1])
def test_minute_update_monotone_stop_and_gap_fill(direction):
    t = position(direction)
    e, old = t['entry'], t['stop']
    ts = pd.Timestamp(t['entry_at'])
    b = pd.Series(dict(Open=e, High=e+120 if direction == 1 else e+5,
                       Low=e-5 if direction == 1 else e-120, Close=e+direction*100,
                       ema=e+direction*80, volatility=5.))
    manage_minute(t, b, ts, 1)
    assert t['status'] == 'OPEN' and t['remaining'] == 1
    assert direction*(t['stop']-old) > 0
    assert pd.Timestamp(t['last_trail_update']) == ts+pd.Timedelta(minutes=1)
    previous = t['stop']
    assert ema_stop(t, e-direction*100, 30)[0] == previous
    opening = previous-direction*10
    b = pd.Series(dict(Open=opening, High=opening+1, Low=opening-1, Close=opening))
    manage_minute(t, b, ts+pd.Timedelta(minutes=1), 1)
    assert t['exit_reason'] == 'STOP_GAP' and t['legs'][0]['price'] == opening


def test_smooth_buffer_without_point_thresholds():
    t = position()
    for threshold in (100, 150):
        t['best_price'] = t['entry']+threshold-.001
        before = ema_stop(t, t['entry'], 10)[1]
        t['best_price'] += .002
        after = ema_stop(t, t['entry'], 10)[1]
        assert 0 < before-after < .001


def test_close_breach_exits_next_open():
    t = position()
    e, ts = t['entry'], pd.Timestamp(t['entry_at'])
    b = pd.Series(dict(Open=e, High=e+100, Low=e-1, Close=e+10, ema=e+50, volatility=2))
    manage_minute(t, b, ts, 1)
    assert t['exit_pending'] and t['status'] == 'OPEN'
    b.Open = e+5
    manage_minute(t, b, ts+pd.Timedelta(minutes=1), 1)
    assert t['exit_reason'] == 'EMA_CLOSE_BREACH' and t['legs'][0]['price'] == e+5


def test_api_interval_cache(monkeypatch):
    from backend.routes import index_swing as route
    calls = []
    def fake(interval):
        calls.append(interval)
        return {'results': [{'status': 'WAIT', 'interval': interval}]}
    monkeypatch.setattr(route, '_scan', fake)
    route._cache.clear()
    try:
        assert route.scan()['results'][0]['interval'] == 1
        route.scan()
        assert route.scan(5)['results'][0]['interval'] == 5
        assert calls == [1, 5]
    finally:
        route._cache.clear()
