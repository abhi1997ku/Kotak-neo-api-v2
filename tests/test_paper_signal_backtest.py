import pytest
from backend.paper_trading import OneMinuteCandle
from screener.paper_signal_backtest import replay

def bars(count):
    return [OneMinuteCandle(29862945+i, 55000-i*10, 55005-i*10, 54995-i*10, 55000-i*10, 100) for i in range(count)]


def test_original_skips_trend_and_fix_enters_only_next_open():
    candles = bars(60)
    assert replay(candles, "original") == []
    signal = replay(candles, "updated")[0]
    assert signal["next_open"] == candles[25].open
    assert signal["forward_5m_points"] == 50
    assert replay(candles[:25], "updated")[0]["next_open"] is None


def test_signal_timing_is_independent_of_future_candles():
    candles = bars(60)
    before = replay(candles[:26], "updated")[0]
    for c in candles[26:]:
        c.open = c.high = c.low = c.close = 60000
    after = replay(candles, "updated")[0]
    assert before["available_at_ist"] == after["available_at_ist"]
    assert before["direction"] == after["direction"]
    assert before["next_open"] == after["next_open"]


def test_missing_horizon_is_not_filled_with_a_later_price():
    candles = bars(60)
    del candles[30]
    assert replay(candles, "updated")[0]["forward_5m_points"] is None


def test_seed_cannot_include_current_or_future_session():
    with pytest.raises(ValueError):
        replay(bars(60), "updated", prior=bars(25))
