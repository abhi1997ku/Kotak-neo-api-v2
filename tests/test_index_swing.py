import pandas as pd
import pytest
from screener.index_swing import levels, backtest, prepare


def test_exact_three_r_both_directions():
    for direction in [-1, 1]:
        p = levels(25000.123, 123.456, direction)
        assert abs(p['target']-p['entry']) == pytest.approx(3*abs(p['entry']-p['stop']))


def test_stop_first_and_adverse_gap():
    data = pd.DataFrame([dict(Open=100, High=101, Low=99, Close=100, atr=2, direction=0)]*203)
    data.loc[200, 'direction'] = 1
    data.loc[201, ['High', 'Low']] = [110, 96]
    assert backtest(data)['total_r'] == -1
    data.loc[201, ['High', 'Low']] = [101, 99]
    data.loc[202, ['Open', 'Low']] = [94, 93]
    assert backtest(data)['total_r'] == -2


def test_incomplete_and_short_session_bars_excluded():
    index = pd.date_range('2026-10-05 09:15', periods=7, freq='h', tz='Asia/Kolkata')
    data = pd.DataFrame(dict(Open=100, High=101, Low=99, Close=100), index=index)
    result = prepare(data, pd.Timestamp('2026-10-05 14:30', tz='Asia/Kolkata'))
    assert len(result) == 5
    assert len(prepare(data, pd.Timestamp('2026-10-05 17:00', tz='Asia/Kolkata'))) == 6
