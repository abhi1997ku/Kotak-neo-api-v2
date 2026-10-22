from dataclasses import replace

import pandas as pd
import pytest

from screener.order_block import OrderBlockConfig, rank_universe, scan


def bars():
    # Confirmed high, opposing candle, displacement break, then later retest.
    values = [(100,102,99,101), (101,105,100,104), (104,104,100,101),
              (101,103,100,102), (102,102,100,101), (101,109,101,108),
              (108,110,106,109), (102,107,100,106), (106,108,104,107)]
    frame = pd.DataFrame(values, columns=["Open", "High", "Low", "Close"],
                         index=pd.date_range("2026-10-12 09:15", periods=len(values), freq="min", tz="Asia/Kolkata"))
    frame["Volume"] = 100
    return frame


def config():
    return OrderBlockConfig(pivot_bars=1, atr_period=2, momentum_bars=4, volume_bars=2,
                            displacement_atr=0.5, min_relative_volume=0, min_efficiency=0,
                            max_stop_atr=10, min_reward_risk=0.1)


def test_activation_and_retest_not_backdated():
    b = bars()
    r = scan(b, b.index[-1]+pd.Timedelta(minutes=1), config())
    z = next(z for z in r["zones"] if z["event"] == "activated")
    assert z["activated_at"] == "2026-10-12T09:21:00+05:30"
    assert z["origin"] == "2026-10-12T09:19:00+05:30"
    assert r["setups"]
    assert all(s["timestamp"] > z["activated_at"] for s in r["setups"])
    assert any(s["accepted"] for s in r["setups"])


@pytest.mark.parametrize("short", [False, True])
def test_every_prefix_is_unchanged(short):
    b = bars()
    if short:
        b[["Open", "High", "Low", "Close"]] = 210-b[["Open", "Low", "High", "Close"]].to_numpy()
    full = scan(b, b.index[-1]+pd.Timedelta(minutes=1), config())
    for cut in range(1, len(b)):
        end = b.index[cut]
        partial = scan(b.iloc[:cut], end, config())
        clipped = scan(b, end, config())
        for key in ("zones", "rankings", "setups"):
            assert partial[key] == clipped[key]
            assert partial[key] == [x for x in full[key] if pd.Timestamp(x["timestamp"]) <= end]


def test_missing_and_future_oi_fail_closed():
    b = bars()
    b["OI"] = range(100, 100+len(b))
    b["OIAvailableAt"] = b.index + pd.Timedelta(days=1)
    r = scan(b, b.index[-1]+pd.Timedelta(minutes=1), replace(config(), require_oi=True), oi_kind="futures")
    assert all(x["oi_change"] is None and "OI_UNAVAILABLE" in x["reason_codes"] for x in r["rankings"])


def test_gap_and_chop_are_rejected():
    b = bars().drop(bars().index[4])
    r = scan(b, b.index[-1]+pd.Timedelta(minutes=1), replace(config(), min_efficiency=1.1))
    assert all("CHOPPY" in x["reason_codes"] for x in r["rankings"])
    assert any("DATA_GAP" in x["reason_codes"] for x in r["rankings"])


def test_duplicates_rejected():
    b = pd.concat([bars(), bars().iloc[-1:]])
    with pytest.raises(ValueError, match="unique"):
        scan(b, b.index[-1], config())


def test_universe_requires_equal_coverage():
    a = {"timestamp": "now", "eligible": True, "score": 50, "missing_features": ["OI"]}
    with pytest.raises(ValueError, match="coverage"):
        rank_universe({"A": {"rankings": [a]}, "B": {"rankings": [{**a, "missing_features": []}]}}, "now")


def test_short_retest_and_valid_oi():
    b = bars()
    b[["Open", "High", "Low", "Close"]] = 210-b[["Open", "Low", "High", "Close"]].to_numpy()
    b["OI"] = range(100, 100+len(b))
    b["OIAvailableAt"] = b.index+pd.Timedelta(minutes=1)
    result = scan(b, b.index[-1]+pd.Timedelta(minutes=1), config(), benchmark=b, oi_kind="futures")
    assert any(x["accepted"] and x["side"] == "short" for x in result["setups"])
    last = result["rankings"][-1]
    assert last["oi_change"] > 0
    assert last["relative_strength"] == 0
    assert last["missing_features"] == []


def test_zone_breach_invalidates_before_retest():
    b = bars()
    b.loc[b.index[7], "Low"] = 99
    result = scan(b, b.index[-1]+pd.Timedelta(minutes=1), config())
    assert any(z["event"] == "invalidated" for z in result["zones"])
    assert not any(x["accepted"] for x in result["setups"])
