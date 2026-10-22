import pandas as pd
from screener.ema_vwap_research import signals


def fixture():
    frames = []
    for day in ("2026-10-05", "2026-10-06"):
        idx = pd.date_range(day+" 09:15", periods=100, freq="min", tz="Asia/Kolkata")
        close = pd.Series([100+i*0.2 for i in range(100)], index=idx)
        b = pd.DataFrame({"Open": close-0.1, "High": close+0.2, "Low": close-3,
                          "Close": close, "Volume": 100}, index=idx)
        frames.append(b)
    return pd.concat(frames)


def test_signals_closed_and_warmup_only():
    b = fixture()
    r = signals(b, b.index[-1]+pd.Timedelta(minutes=1))
    assert r
    assert all(pd.Timestamp(s["timestamp"]).day == 6 for s in r)
    assert all(pd.Timestamp(s["timestamp"]).minute % 5 == 0 for s in r)
    assert all(s["stop"] < s["entry"] < s["target"] for s in r)


def test_prefix_and_incomplete_bar_do_not_change_signals():
    b = fixture()
    full = signals(b, b.index[-1]+pd.Timedelta(minutes=1))
    for cut in (130, 133, 155, 181):
        at = b.index[cut]
        earlier = signals(b.iloc[:cut], at)
        assert earlier == signals(b, at)
        assert earlier == [s for s in full if pd.Timestamp(s["timestamp"]) <= at]


def test_diagnostics_preserve_signals_and_prefix():
    b = fixture()
    b.loc[b.index.date == pd.Timestamp('2026-10-06').date(), 'Low'] = b.Close - 0.15
    end = b.index[-1] + pd.Timedelta(minutes=1)
    diagnostics = []
    assert signals(b, end, diagnostics=diagnostics) == signals(b, end)
    assert any('NO_EMA_TOUCH' in r['reason_codes'] for r in diagnostics)
    assert all(r['reason_codes'] and not r['accepted'] for r in diagnostics)
    cut = b.index[151]
    earlier = []
    signals(b.iloc[:151], cut, diagnostics=earlier)
    assert earlier == [r for r in diagnostics if pd.Timestamp(r['timestamp']) <= cut]
