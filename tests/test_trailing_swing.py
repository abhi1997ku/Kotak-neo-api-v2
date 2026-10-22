import json
import pandas as pd
import pytest

from screener.trailing_swing import analyze, features, make_plan, manage_bar, open_trade, public_trade


def trade(direction=1):
    row = pd.Series(dict(direction=direction, width=40., upper=1000., lower=1000.))
    plan = make_plan("NIFTY", row, pd.Timestamp("2026-10-05 09:15", tz="Asia/Kolkata"))
    return open_trade(plan, plan["entry_trigger"], pd.Timestamp("2026-10-05 10:15", tz="Asia/Kolkata"))


def bar(o, h, l, c):
    return pd.Series(dict(Open=o, High=h, Low=l, Close=c))


def context(low, high):
    return pd.Series(dict(width=40., structure_low=low, structure_high=high))


def history():
    dates = pd.bdate_range("2025-01-01", periods=30, tz="Asia/Kolkata")
    index = pd.DatetimeIndex([d+pd.Timedelta(hours=9+h, minutes=15) for d in dates for h in range(7)])
    values = [20000+i*5 for i in range(len(index))]
    return pd.DataFrame({"Open": values, "High": [v+8 for v in values],
                         "Low": [v-4 for v in values], "Close": [v+5 for v in values]}, index=index)


@pytest.mark.parametrize("direction", [1, -1])
def test_trailing_stop_never_loosens_and_is_not_retroactive(direction):
    t = trade(direction)
    entry, old = t["entry"], t["stop"]
    if direction == 1:
        candle = bar(entry, entry+120, entry-10, entry+100)
    else:
        candle = bar(entry, entry+10, entry-120, entry-100)
    timestamp = pd.Timestamp(t["entry_at"])
    manage_bar(t, candle, timestamp, context(candle.Low, candle.High))
    assert t["status"] == "OPEN"  # New trailing level wasn't active earlier.
    assert direction*(t["stop"]-old) > 0
    tightened = t["stop"]
    close = candle.Close
    manage_bar(t, bar(close, close+1, close-1, close), timestamp+pd.Timedelta(hours=1), context(close-300, close+300))
    assert direction*(t["stop"]-tightened) >= 0


def test_new_entry_does_not_fill_stop_at_an_open_before_entry_trigger():
    t = trade()
    old_stop = t["stop"]
    manage_bar(t, bar(old_stop-50, t["entry"]+1, old_stop-60, t["entry"]), pd.Timestamp(t["entry_at"]), new_entry=True)
    assert t["legs"][0]["price"] == old_stop  # Conservative intrabar stop, no fictitious pre-entry gap.


def test_existing_stop_gap_is_filled_at_actual_open():
    t = trade()
    opening = t["stop"]-20
    manage_bar(t, bar(opening, opening+1, opening-1, opening), pd.Timestamp(t["entry_at"]))
    assert t["exit_reason"] == "STOP_GAP"
    assert t["legs"][0]["price"] == opening


def test_full_position_runs_past_milestones_until_stop_exit():
    t = trade()
    entry = t["entry"]
    ts = pd.Timestamp(t["entry_at"])
    manage_bar(t, bar(entry, entry+400, entry-1, entry+395), ts, context(entry-20, entry+400))
    assert t["status"] == "OPEN" and not t["legs"]
    manage_bar(t, bar(entry+400, entry+410, entry+395, entry+405), ts+pd.Timedelta(hours=1), context(entry+300, entry+410))
    assert t["status"] == "OPEN" and t["remaining"] == 1.
    assert not t["legs"]
    assert t["target"] is None
    manage_bar(t, bar(entry+405, entry+406, entry+380, entry+385), ts+pd.Timedelta(hours=2))
    assert len(t["legs"]) == 1 and t["legs"][0]["fraction"] == 1.
    assert t["exit_reason"] == "TRAIL_STOP"
    assert public_trade(t)["realized_net_points"] > 0


def test_stop_gap_closes_the_full_position():
    t = trade()
    opening = t["stop"]-10
    manage_bar(t, bar(opening, opening+1, opening-1, opening), pd.Timestamp(t["entry_at"]))
    assert len(t["legs"]) == 1 and t["legs"][0]["fraction"] == 1
    assert t["legs"][0]["kind"] == "STOP_GAP"


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("movement,stage,distance", [(99, "NORMAL", 72), (100, "TIGHT", 30), (149, "TIGHT", 30), (150, "FAST", 20)])
def test_acceleration_boundaries(direction, movement, stage, distance):
    t = trade(direction)
    e = t["entry"]
    extreme = e+direction*movement
    candle = bar(e, extreme if direction == 1 else e+1, e-1 if direction == 1 else extreme, extreme-direction*2)
    manage_bar(t, candle, pd.Timestamp(t["entry_at"]), context(candle.Low, candle.High))
    assert t["trail_stage"] == stage
    assert t["stop"] == pytest.approx(extreme-direction*distance)
    assert t["remaining"] == 1


def test_new_trail_breached_at_close_exits_next_open_not_retroactively():
    t = trade()
    e, ts = t["entry"], pd.Timestamp(t["entry_at"])
    manage_bar(t, bar(e, e+180, e-1, e+100), ts, context(e-1, e+180))
    assert t["exit_pending"] and t["status"] == "OPEN" and not t["legs"]
    manage_bar(t, bar(e+90, e+95, e+80, e+85), ts+pd.Timedelta(hours=1))
    assert t["exit_reason"] == "TRAIL_CLOSE_BREACH"
    assert t["legs"][0]["price"] == e+90


def test_features_are_causal_and_trades_are_visible_without_accuracy_gate():
    raw = history()
    _, prefix = features(raw.iloc[:150])
    _, full = features(raw)
    pd.testing.assert_frame_equal(prefix, full.loc[prefix.index])
    result = analyze(raw, "NIFTY", now=raw.index[-1]+pd.Timedelta(minutes=15))
    json.dumps(result, allow_nan=False)
    assert result["trade"] or result["setup"]
    assert result["status"] != "RESEARCH_ONLY"
    assert result["desired_move_points"] == [200, 330]
    assert result["target"] is None


def test_stale_data_keeps_historical_trade_visible_but_is_not_actionable():
    raw = history()
    result = analyze(raw, "BANKNIFTY", now=raw.index[-1]+pd.Timedelta(days=10))
    assert result["data_status"] == "STALE"
    assert result["actionable"] is False
    assert result["trade"] or result["setup"]
    assert result["desired_move_points"] == [500, 600]
