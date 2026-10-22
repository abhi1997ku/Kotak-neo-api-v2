import numpy as np
import pandas as pd
from screener.support_resistance import add_levels


def bars():
    index = pd.date_range('2026-10-08 09:15', periods=6, freq='h', tz='Asia/Kolkata')
    return pd.DataFrame(dict(Open=[100]*6, Close=[100]*6,
                             High=[102, 104, 110, 104, 103, 102],
                             Low=[98, 96, 90, 96, 97, 98]), index=index)


def test_pivots_require_two_right_bars_and_are_causal():
    raw = bars()
    result = add_levels(raw, raw)
    assert np.isnan(result.support.iloc[3])
    assert result.support.iloc[4] == 90
    assert result.resistance.iloc[4] == 110
    pd.testing.assert_frame_equal(result.iloc[:4], add_levels(raw.iloc[:4], raw.iloc[:4]))
    changed = raw.copy()
    changed.iloc[-1, changed.columns.get_loc('High')] = 999
    pd.testing.assert_frame_equal(result.iloc[:-1], add_levels(changed, changed).iloc[:-1])


def test_previous_session_only_and_broken_support_removed():
    raw = bars()
    second = raw.copy()
    second.index += pd.Timedelta(days=1)
    second['High'] = 150
    full = pd.concat([raw, second])
    result = add_levels(full, full)
    assert result.previous_session_high.iloc[-1] == 110
    assert result.previous_session_low.iloc[-1] == 90
    assert result.previous_session_high.iloc[:6].isna().all()
    raw.iloc[-1, raw.columns.get_loc('Close')] = 89
    assert np.isnan(add_levels(raw, raw).support.iloc[-1])
