import json
import numpy as np
import pandas as pd
import pytest

from screener.index_pattern import (
    FEATURES, HORIZON, PatternModel, analyze, clean_bars, levels,
    make_labels, outcome, prepare, simulate, session_gaps,
)


@pytest.fixture(scope="module")
def history():
    rng = np.random.default_rng(17)
    dates = pd.bdate_range("2024-01-01", periods=190, tz="Asia/Kolkata")
    index = pd.DatetimeIndex([d+pd.Timedelta(hours=9, minutes=15)+pd.Timedelta(hours=h) for d in dates for h in range(7)])
    close = 20000+np.cumsum(rng.normal(0, 30, len(index)))
    opened = np.r_[close[0]-10, close[:-1]]+rng.normal(0, 4, len(index))
    return pd.DataFrame({"Open": opened, "High": np.maximum(opened, close)+rng.uniform(5, 20, len(index)),
                         "Low": np.minimum(opened, close)-rng.uniform(5, 20, len(index)), "Close": close}, index=index)


def flat_bars():
    return pd.DataFrame({"Open": 100., "High": 101., "Low": 99., "Close": 100.},
                        index=pd.date_range("2025-01-01", periods=HORIZON+5, freq="h", tz="UTC"))


def test_custom_features_are_prefix_invariant(history):
    before = prepare(history.iloc[:700])
    after = prepare(history)
    pd.testing.assert_frame_equal(before, after.loc[before.index])
    assert not {"rsi", "fast", "slow", "atr"}.intersection(after.columns)


def test_completed_hours_and_closing_segment(history):
    now = history.index[5]+pd.Timedelta(minutes=30)
    assert len(prepare(history, now)) == 5
    assert len(clean_bars(history, history.index[6]+pd.Timedelta(minutes=15))) == 7
    assert len(prepare(history, history.index[6]+pd.Timedelta(minutes=15))) == 6


@pytest.mark.parametrize("direction", [-1, 1])
def test_three_r_after_price_rounding(direction):
    p = levels(25000.123, 123.456, direction)
    assert abs(p["target"]-p["entry"]) == pytest.approx(3*abs(p["entry"]-p["stop"]))


@pytest.mark.parametrize("direction", [-1, 1])
def test_ambiguous_candle_stops_first(direction):
    bars = flat_bars()
    bars.iloc[0, bars.columns.get_loc("High")] = 110
    bars.iloc[0, bars.columns.get_loc("Low")] = 90
    result = outcome(bars, 0, 2, direction)
    assert result["kind"] == "STOP"
    assert result["gross_r"] == -1
    assert result["net_r"] < -1


def test_adverse_stop_gap_and_favourable_target_gap():
    bars = flat_bars()
    bars.loc[bars.index[1], ["Open", "High", "Low", "Close"]] = [94, 95, 93, 94]
    assert outcome(bars, 0, 2, 1)["gross_r"] == -3
    # Target fills at the open before this bar's subsequent reversal.
    bars.loc[bars.index[1], ["Open", "High", "Low", "Close"]] = [108, 110, 90, 95]
    assert outcome(bars, 0, 2, 1)["kind"] == "TARGET"
    assert outcome(bars, 0, 2, 1)["gross_r"] == 3


def test_timeout_and_unmatured_labels():
    bars = flat_bars()
    assert outcome(bars, 0, 2, 1)["kind"] == "TIMEOUT"
    assert outcome(bars, 0, 2, 1)["net_r"] < 0
    assert outcome(bars.iloc[:HORIZON-1], 0, 2, 1) is None


def test_next_open_and_partial_session_are_used(history):
    features = prepare(history)
    bars = clean_bars(history)
    labels = make_labels(features.iloc[:40], bars)
    closing_signal = next(r for r in labels if r["timestamp"].hour == 14)
    entry = closing_signal["long"]["start"]
    assert bars.index[entry].hour == 15
    assert closing_signal["long"]["entry"] == round(bars.iloc[entry].Open, 2)


def test_purged_model_cannot_see_future_labels(history):
    features, bars = prepare(history), clean_bars(history)
    labels = make_labels(features, bars)
    cutoff = features.index[600]
    train = [r for r in labels if r["mature_at"] < cutoff]
    model = PatternModel(features, train)
    modified = features.copy()
    modified.loc[modified.index >= cutoff, list(FEATURES)] = 99999
    other = PatternModel(modified, train)
    np.testing.assert_array_equal(model.center, other.center)
    np.testing.assert_array_equal(model.scale, other.scale)
    np.testing.assert_array_equal(model.weights, other.weights)
    assert all(r["mature_at"] < cutoff for r in model.labels)


def test_missing_candles_excluded_from_features_and_labels(history):
    broken = history.drop(history.index[702])
    bars, features = clean_bars(broken), prepare(broken)
    boundaries = bars.index[session_gaps(bars)]
    assert len(boundaries) == 1
    assert features.loc[boundaries[0], list(FEATURES)].isna().all()
    labels = make_labels(features, bars)
    assert all(not (r["timestamp"] < boundaries[0] <= r["mature_at"]) for r in labels)


def test_endpoint_cache_isolated_and_errors_not_cached(monkeypatch):
    from backend.routes import index_swing
    calls = []
    def scan(*args):
        calls.append(1)
        return {"results": [{"symbol": "NIFTY", "status": "WAIT"}]}
    monkeypatch.setattr(index_swing, "_scan", scan)
    index_swing._cache.clear()
    try:
        first = index_swing.scan()
        first["results"][0]["status"] = "ALTERED"
        assert index_swing.scan()["results"][0]["status"] == "WAIT"
        assert len(calls) == 1
        index_swing._cache.clear()
        monkeypatch.setattr(index_swing, "_scan", lambda *args: {"results": [{"status": "UNAVAILABLE"}]})
        index_swing.scan()
        assert not index_swing._cache
    finally:
        index_swing._cache.clear()


def test_single_position_and_timeout_not_counted_as_target_win():
    bars = flat_bars()
    labels = [{"timestamp": bars.index[i], "long": outcome(bars, i+1, 2, 1)} for i in range(3)]
    prediction = {"score": .6, "mean_net_r": .1, "side": "long"}
    report = simulate([(r, prediction) for r in labels], .5, bars)
    assert report["closed_trades"] == 1
    assert report["timeouts"] == 1
    assert report["wins"] == 0


def test_analysis_returns_json_and_blocks_unproven_live_signals(history):
    report = analyze(history, now=history.index[-1]+pd.Timedelta(minutes=15))
    json.dumps(report, allow_nan=False)
    assert report["backtest"]["accuracy_verified"] is False
    assert report["model"]["test_start"] == report["backtest"]["period_start"]
    if not report["model"]["evidence_gate_passed"]:
        assert report["status"] == "RESEARCH_ONLY"
        assert report["setup"] is None


def test_invalid_prices_and_short_history_rejected(history):
    broken = history.copy()
    broken.iloc[1, broken.columns.get_loc("High")] = -1
    with pytest.raises(ValueError, match="Invalid"):
        clean_bars(broken)
    with pytest.raises(ValueError, match="1000"):
        analyze(history.iloc[:300])
