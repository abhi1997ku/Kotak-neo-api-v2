import numpy as np
import pandas as pd
import pytest

from screener.index_experiments import (
    FEATURE_SETS, build_labels, chart_features, metrics, partitions, selection_key, supported,
)
from screener.index_pattern import clean_bars, outcome


@pytest.fixture(scope="module")
def candles():
    rng = np.random.default_rng(8)
    dates = pd.bdate_range("2024-01-01", periods=190, tz="Asia/Kolkata")
    index = pd.DatetimeIndex([d+pd.Timedelta(hours=9+h, minutes=15) for d in dates for h in range(7)])
    close = 20000+np.cumsum(rng.normal(0, 25, len(index)))
    opened = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"Open": opened, "High": np.maximum(opened, close)+12,
                         "Low": np.minimum(opened, close)-12, "Close": close}, index=index)


def test_patterns_and_indicators_do_not_use_future_candles(candles):
    full = chart_features(candles)
    prefix = chart_features(candles.iloc[:700])
    pd.testing.assert_frame_equal(prefix, full.loc[prefix.index])
    assert full[list(FEATURE_SETS["hybrid"])].iloc[200].notna().all()


def test_all_horizons_keep_three_r_and_purge_at_true_close(candles):
    bars, features = clean_bars(candles), chart_features(candles)
    for horizon in (21, 42, 70):
        labels = build_labels(features, bars, .5, horizon)
        training, validation = partitions(features, labels, .35, .45)
        cutoff, end = features.index[int(len(features)*.35)], features.index[int(len(features)*.45)]
        assert training and validation
        assert all(r["mature_at"] < cutoff for r in training)
        assert all(cutoff <= r["timestamp"] < r["mature_at"] < end for r in validation)
        for side in ("long", "short"):
            trade = labels[0][side]
            assert abs(trade["target"]-trade["entry"]) == pytest.approx(3*abs(trade["stop"]-trade["entry"]))
            assert trade["mature_end"]-trade["start"]+1 == horizon


def test_search_ranking_rejects_perfect_tiny_samples():
    small = {"validation": {"closed_trades": 3, "win_rate_interval": [43., 100.], "mean_r": 3.},
             "folds": [{"closed_trades": 1}]*3}
    larger = {"validation": {"closed_trades": 40, "win_rate_interval": [30., 60.], "mean_r": .6},
              "folds": [{"closed_trades": 13}]*3}
    assert not supported(small)
    assert supported(larger)
    assert selection_key(larger) > selection_key(small)


def test_later_gaps_exclude_all_crossing_label_horizons(candles):
    damaged = candles.drop(candles.index[800])
    bars, features = clean_bars(damaged), chart_features(damaged)
    labels = build_labels(features, bars, 1, 70)
    boundary = candles.index[801]
    assert not any(r["timestamp"] < boundary <= r["mature_at"] for r in labels)


def test_extra_metrics_and_cost_stress(candles):
    bars = clean_bars(candles)
    labels = [{"timestamp": bars.index[i-1], "long": outcome(bars, i, 60, 1)} for i in (5, 40, 75)]
    predictions = [(r, {"side": "long", "score": .8, "mean_net_r": 1., "long_score": .8, "short_score": .2})
                   for r in labels]
    # Both-direction labels are required for calibration diagnostics.
    for label in labels:
        label["short"] = outcome(bars, label["long"]["start"], 60, -1)
    report = metrics(predictions, .5, bars)
    assert report["closed_trades"] == 3
    assert report["double_cost_net_r"] < report["total_r"]
    assert 0 <= report["brier_score_all_patterns"] <= 1
    assert report["long_trades"] == 3 and report["short_trades"] == 0
    assert sum(m["trades"] for m in report["monthly"].values()) == 3


def test_variable_horizon_timeout():
    bars = pd.DataFrame({"Open": 100., "High": 101., "Low": 99., "Close": 100.}, index=range(75))
    assert outcome(bars, 0, 2, 1, horizon=70)["end"] == 69
    assert outcome(bars.iloc[:60], 0, 2, 1, horizon=70) is None
    with pytest.raises(ValueError):
        outcome(bars, 0, 2, 1, horizon=0)


def test_saved_experiment_endpoint_handles_missing_and_corrupt_reports(tmp_path, monkeypatch):
    from backend.routes import index_swing
    from fastapi import HTTPException
    path = tmp_path / "latest_search.json"
    monkeypatch.setattr(index_swing, "_research_report", path)
    assert index_swing.experiments() == {"status": "NOT_RUN", "results": []}
    path.write_text('{"results": [{"symbol": "NIFTY"}]}', encoding="utf-8")
    assert index_swing.experiments()["results"][0]["symbol"] == "NIFTY"
    path.write_text("invalid", encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        index_swing.experiments()
    assert error.value.status_code == 503
