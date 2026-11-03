import pandas as pd
from screener.intraday_stocks import rank
from backend.intraday_stock_store import dashboard


def fixture():
    now = pd.Timestamp("2026-11-02 09:45", tz="Asia/Kolkata")
    dates = pd.bdate_range(end=now.normalize(), periods=21)
    index = pd.DatetimeIndex([d+pd.Timedelta(hours=9, minutes=15+i) for d in dates for i in range(30)])
    frame = pd.DataFrame(dict(Open=100., High=102., Low=99., Close=100., Volume=10000.), index=index)
    benchmark = frame.copy()
    today = frame.index.date == now.date()
    frame.loc[today, "Close"] = [100+i*.04 for i in range(30)]
    frame.loc[today, "Volume"] = 30000
    return now, frame, benchmark


def test_rank_and_no_future_leak():
    now, frame, benchmark = fixture()
    rows, _ = rank({"A": frame}, benchmark, now)
    assert rows[0]["side"] == "long"
    future = frame.iloc[-1:].copy()
    future.index = future.index+pd.Timedelta(minutes=1)
    future.Close = 10000
    future.High = 10001
    assert rank({"A": pd.concat([frame, future])}, benchmark, now)[0] == rows


def test_missing_minutes_and_history_block():
    now, frame, benchmark = fixture()
    assert not rank({"A": frame.iloc[:-1]}, benchmark, now)[0]
    assert not rank({"A": frame.iloc[-30:]}, benchmark, now)[0]
    assert not rank({"A": frame}, benchmark.iloc[:-1], now)[0]


def test_top_three_and_no_data(tmp_path):
    now, frame, benchmark = fixture()
    rows, _ = rank({s: frame for s in "EDCBA"}, benchmark, now)
    assert [r["symbol"] for r in rows] == ["A", "B", "C"]
    report = dashboard(tmp_path)
    assert report["state"] == "DATA REQUIRED"
    assert not report["orders_enabled"] and not report["fills_enabled"]


def test_invalid_future_candle_does_not_change_past_selection():
    now, frame, benchmark = fixture()
    future = frame.iloc[-1:].copy()
    future.index = pd.DatetimeIndex([now])
    future.Close = 100000  # Invalid but unavailable at selection time.
    assert rank({"A": pd.concat([frame, future])}, benchmark, now) == rank({"A": frame}, benchmark, now)
    invalid = frame.copy()
    invalid.iloc[-1, invalid.columns.get_loc("Close")] = 100000
    assert not rank({"A": invalid}, benchmark, now)[0]


def test_stronger_setup_wins_over_alphabetical_tie():
    now, frame, benchmark = fixture()
    strong = frame.copy()
    strong.loc[strong.index.date == now.date(), "Volume"] *= 5
    rows, _ = rank({"AAA": frame, "ZZZ": strong}, benchmark, now)
    assert [r["symbol"] for r in rows] == ["ZZZ", "AAA"]
    assert 0 < rows[1]["score"] < rows[0]["score"] < 100
