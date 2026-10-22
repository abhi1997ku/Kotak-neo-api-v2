import pandas as pd
from screener.order_block_research import outcomes


def run(values, side="long", entry_minutes=1):
    b = pd.DataFrame(values, columns=["Open", "High", "Low", "Close"], index=pd.date_range("2026-10-16 14:46", periods=len(values), freq="min", tz="Asia/Kolkata"))
    setup = {"accepted": True, "timestamp": b.index[0].isoformat(), "side": side,
             "entry": 100, "stop": 95 if side == "long" else 105, "target": 110 if side == "long" else 90}
    return outcomes(b, [setup], entry_minutes=entry_minutes)[0]


def test_next_bar_expires():
    assert run([(98, 99, 97, 98), (100, 112, 99, 111)])["result"] == "EXPIRED_UNFILLED"


def test_intrabar_stop_cannot_fill_at_preentry_open():
    t = run([(94, 102, 93, 101)])
    assert t["exit_price"] == 95
    assert t["points"] == -5
    assert t["ohlc_order_uncertain"]


def test_adverse_gap_stop():
    t = run([(102, 104, 101, 103), (90, 92, 89, 91)])
    assert t["fill"] == 102
    assert t["exit_price"] == 90
    assert t["points"] == -12


def test_both_touched_stop_first():
    assert run([(100, 111, 94, 105)])["result"] == "STOP"


def test_short_target():
    assert run([(100, 102, 89, 90)], "short")["points"] == 10


def test_missing_exit_not_flattened():
    t = run([(100, 102, 99, 101)])
    assert t["result"] == "INCOMPLETE_DATA"
    assert t["points"] is None


def test_three_minute_entry_can_fill_second_bar():
    t = run([(98, 99, 97, 98), (100, 111, 99, 110)], entry_minutes=3)
    assert t["result"] == "TARGET"
    assert "14:47" in t["fill_at"]


def test_pending_stop_breach_cancels_before_later_trigger():
    t = run([(98, 99, 94, 98), (100, 111, 99, 110)], entry_minutes=3)
    assert t["result"] == "INVALIDATED_BEFORE_ENTRY"
    assert "fill" not in t
