"""Bounded, reproducible chart-model search with purged walk-forward selection.

Run python -m screener.index_experiments. Cached candles are never downloaded
or overwritten by this runner. Every trial, including failures, is retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from screener.index_pattern import FEATURES, clean_bars, outcome, prepare, session_gaps, simulate, wilson

PATTERNS = (
    "bull_engulf", "bear_engulf", "lower_pin", "upper_pin", "inside_bar", "outside_bar",
    "breakout_up", "breakout_down", "double_top", "double_bottom", "head_shoulders",
    "inverse_head_shoulders", "channel_position", "compression_24", "slope_change", "session_hour",
)
INDICATORS = ("rsi_14", "ema_gap", "ema_slope", "range_zscore", "return_24", "return_48")
FEATURE_SETS = {"core": FEATURES, "patterns": FEATURES+PATTERNS, "hybrid": FEATURES+PATTERNS+INDICATORS}
FOLDS = ((.35, .45), (.45, .575), (.575, .70))
THRESHOLDS = (.20, .30, .40, .50, .60, .70, .80, .90)
MODEL_NAMES = ("boost7", "boost15", "extra_trees", "random_forest")
RISK_SCALES = (.50, .75, 1.0, 1.25)
HORIZONS = (21, 42, 70)
MIN_VALIDATION_TRADES = 30
MIN_FOLD_TRADES = 5
VERSION = "chart-search-v1"


def chart_features(raw):
    bars, f = clean_bars(raw), prepare(raw)
    o, h, l, c = f.Open, f.High, f.Low, f.Close
    width = (h-l).replace(0, np.nan)
    body = c-o
    prior_h, prior_l = h.shift().rolling(24).max(), l.shift().rolling(24).min()
    f["bull_engulf"] = ((body > 0) & (body.shift() < 0) & (o <= c.shift()) & (c >= o.shift())).astype(float)
    f["bear_engulf"] = ((body < 0) & (body.shift() > 0) & (o >= c.shift()) & (c <= o.shift())).astype(float)
    f["lower_pin"] = (pd.concat([o, c], axis=1).min(axis=1)-l)/width
    f["upper_pin"] = (h-pd.concat([o, c], axis=1).max(axis=1))/width
    f["inside_bar"] = ((h < h.shift()) & (l > l.shift())).astype(float)
    f["outside_bar"] = ((h > h.shift()) & (l < l.shift())).astype(float)
    f["breakout_up"] = (c-prior_h)/f.risk_points
    f["breakout_down"] = (prior_l-c)/f.risk_points
    # Completed separated windows describe pattern geometry. No centered
    # rolling windows or retrospective pivot detection is used.
    left_h, right_h = h.shift(12).rolling(6).max(), h.shift().rolling(6).max()
    left_l, right_l = l.shift(12).rolling(6).min(), l.shift().rolling(6).min()
    f["double_top"] = np.exp(-(left_h-right_h).abs()/f.risk_points)*(right_h-c)/f.risk_points
    f["double_bottom"] = np.exp(-(left_l-right_l).abs()/f.risk_points)*(c-right_l)/f.risk_points
    center_h, center_l = h.shift(6).rolling(6).max(), l.shift(6).rolling(6).min()
    f["head_shoulders"] = ((center_h > left_h) & (center_h > right_h)).astype(float)*f.double_top
    f["inverse_head_shoulders"] = ((center_l < left_l) & (center_l < right_l)).astype(float)*f.double_bottom
    f["channel_position"] = (c-prior_l)/(prior_h-prior_l).replace(0, np.nan)
    f["compression_24"] = width.rolling(6).mean()/width.rolling(24).mean()
    f["slope_change"] = (c.diff(6)-c.shift(6).diff(6))/f.risk_points
    f["session_hour"] = (f.index.tz_convert("Asia/Kolkata").hour-9)/5
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1/14, adjust=False).mean()
    f["rsi_14"] = (gain/(gain+loss).replace(0, np.nan)).fillna(.5)
    fast, slow = c.ewm(span=12, adjust=False).mean(), c.ewm(span=48, adjust=False).mean()
    f["ema_gap"] = (fast-slow)/f.risk_points
    f["ema_slope"] = slow.diff(6)/f.risk_points
    f["range_zscore"] = (width-width.rolling(24).mean())/width.rolling(24).std().replace(0, np.nan)
    f["return_24"], f["return_48"] = c.diff(24)/f.risk_points, c.diff(48)/f.risk_points
    counts = np.r_[0, np.cumsum(session_gaps(bars))]
    positions = bars.index.get_indexer(f.index)
    columns = f.columns.get_indexer(list(FEATURE_SETS["hybrid"]))
    for i, position in enumerate(positions):
        if i < 96 or counts[position+1]-counts[positions[max(0, i-96)]]:
            f.iloc[i, columns] = np.nan
    return f.replace([np.inf, -np.inf], np.nan)


def build_labels(features, bars, risk_scale, horizon):
    prices = bars[["Open", "High", "Low", "Close"]].to_numpy(float)
    gaps = np.r_[0, np.cumsum(session_gaps(bars))]
    eligible = features[list(FEATURE_SETS["hybrid"])].notna().all(axis=1).to_numpy()
    starts = bars.index.searchsorted(features.index+pd.Timedelta(hours=1))
    risks = (features.risk_points*risk_scale).clip(lower=features.Close*.0005).round(2).to_numpy()
    labels = []
    for i in np.flatnonzero(eligible):
        start = int(starts[i])
        if start+horizon > len(bars) or gaps[start+horizon]-gaps[start]:
            continue
        long = outcome(bars, start, risks[i], 1, prices=prices, horizon=horizon)
        short = outcome(bars, start, risks[i], -1, prices=prices, horizon=horizon)
        end = bars.index[start+horizon-1]
        minutes = 15 if end.tz_convert("Asia/Kolkata").hour == 15 else 60
        labels.append({"feature_index": int(i), "timestamp": features.index[i],
                       "mature_at": end+pd.Timedelta(minutes=minutes), "long": long, "short": short})
    return labels


def partitions(features, labels, begin, end):
    start_at, end_at = features.index[int(len(features)*begin)], features.index[int(len(features)*end)]
    training = [r for r in labels if r["mature_at"] < start_at]
    evaluation = [r for r in labels if start_at <= r["timestamp"] < end_at and r["mature_at"] < end_at]
    return training, evaluation


def estimator(name):
    from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
    if name.startswith("boost"):
        return HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=int(name[5:]),
                                              min_samples_leaf=30, l2_regularization=5,
                                              learning_rate=.06, early_stopping=False, random_state=17)
    constructor = ExtraTreesClassifier if name == "extra_trees" else RandomForestClassifier
    return constructor(n_estimators=100, max_depth=7, min_samples_leaf=20,
                       max_features=.8, n_jobs=2, random_state=17)


def fit_predict(features, training, evaluation, feature_set, model_name):
    columns = list(FEATURE_SETS[feature_set])
    x = features.iloc[[r["feature_index"] for r in training]][columns].to_numpy(float)
    query = features.iloc[[r["feature_index"] for r in evaluation]][columns].to_numpy(float)
    if len(x) < 200 or not len(query):
        raise ValueError("Insufficient purged train/evaluation data")
    scores = {}
    for side in ("long", "short"):
        y = np.array([r[side]["kind"] == "TARGET" for r in training], dtype=int)
        if len(np.unique(y)) == 1:
            scores[side] = np.full(len(query), float(y[0]))
        else:
            model = estimator(model_name)
            model.fit(x, y)
            scores[side] = model.predict_proba(query)[:, list(model.classes_).index(1)]
    output = []
    for i, label in enumerate(evaluation):
        side = "long" if scores["long"][i] >= scores["short"][i] else "short"
        # This search uses classifier threshold selection; it does not inherit
        # the analog model's separate expected-R filter.
        prediction = {"side": side, "score": float(scores[side][i]), "mean_net_r": 1.,
                      "long_score": float(scores["long"][i]), "short_score": float(scores["short"][i])}
        output.append((label, prediction))
    return output


def metrics(predictions, threshold, bars):
    report = simulate(predictions, threshold, bars)
    trades = report["trades"]
    report["signal_coverage_pct"] = round(100*len(trades)/len(predictions), 3) if predictions else 0
    report["long_trades"] = sum(t["direction"] == "LONG" for t in trades)
    report["short_trades"] = len(trades)-report["long_trades"]
    report["median_net_r"] = round(float(np.median([t["net_r"] for t in trades])), 4) if trades else None
    report["long_target_rate"] = _side_rate(trades, "LONG")
    report["short_target_rate"] = _side_rate(trades, "SHORT")
    monthly = {}
    for t in trades:
        month = t["exit_at"][:7]
        monthly.setdefault(month, {"trades": 0, "wins": 0, "net_r": 0.})
        monthly[month]["trades"] += 1
        monthly[month]["wins"] += t["outcome"] == "TARGET"
        monthly[month]["net_r"] = round(monthly[month]["net_r"]+t["net_r"], 4)
    report["monthly"] = monthly
    if predictions:
        errors = [(p[f"{side}_score"]-float(r[side]["kind"] == "TARGET"))**2
                  for r, p in predictions for side in ("long", "short")]
        report["brier_score_all_patterns"] = round(float(np.mean(errors)), 5)
    return report


def _side_rate(trades, side):
    chosen = [t for t in trades if t["direction"] == side]
    return round(100*sum(t["outcome"] == "TARGET" for t in chosen)/len(chosen), 2) if chosen else None


def supported(trial):
    return (trial["validation"]["closed_trades"] >= MIN_VALIDATION_TRADES
            and all(f["closed_trades"] >= MIN_FOLD_TRADES for f in trial["folds"]))


def selection_key(trial):
    summary = trial["validation"]
    interval = summary["win_rate_interval"]
    return (bool(supported(trial)), interval[0] if interval else -1,
            summary["mean_r"] if summary["mean_r"] is not None else -100)


def research(raw, symbol, folder, quick=False):
    bars, features = clean_bars(raw), chart_features(raw)
    if len(features) < 1000:
        raise ValueError("At least 1000 hourly candles required")
    scales = (.5, 1.) if quick else RISK_SCALES
    horizons = (21,) if quick else HORIZONS
    groups = ("patterns",) if quick else tuple(FEATURE_SETS)
    models = ("boost7",) if quick else MODEL_NAMES
    trials = []
    jobs = list(itertools.product(scales, horizons, groups, models))
    label_cache = {}
    started = time.monotonic()
    trial_path = folder / f"{symbol}_trials.jsonl"
    for number, (scale, horizon, group, name) in enumerate(jobs, 1):
        key = (scale, horizon)
        if key not in label_cache:
            label_cache[key] = build_labels(features, bars, scale, horizon)
        labels = label_cache[key]
        fold_predictions = []
        for begin, end in FOLDS:
            training, evaluation = partitions(features, labels, begin, end)
            fold_predictions.append(fit_predict(features, training, evaluation, group, name))
        # All thresholds see exactly the same out-of-training predictions.
        for threshold in THRESHOLDS:
            folds = [simulate(p, threshold, bars) for p in fold_predictions]
            aggregate = metrics([item for fold in fold_predictions for item in fold], threshold, bars)
            trial = {"config": {"risk_scale": scale, "horizon": horizon, "features": group,
                                "model": name, "threshold": threshold},
                     "validation": {k: v for k, v in aggregate.items() if k != "trades"},
                     "folds": [{k: f[k] for k in ("closed_trades", "win_rate", "total_r")} for f in folds]}
            trials.append(trial)
            with trial_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(trial, allow_nan=False)+"\n")
        best = max(trials, key=selection_key)
        print(f"{symbol}: {number}/{len(jobs)} model/stop/horizon combinations; {len(trials)} thresholds evaluated; "
              f"best supported validation {best['validation']['win_rate']}% / {best['validation']['closed_trades']} trades; "
              f"{time.monotonic()-started:.0f}s", flush=True)
    ranked = sorted(trials, key=selection_key, reverse=True)
    chosen = ranked[0]
    # Freeze winner before reading its final-period results. No search trial
    # is ranked by final-period metrics, nor is a failed winner replaced.
    (folder / f"{symbol}_selection.json").write_text(json.dumps(chosen, indent=2, allow_nan=False), encoding="utf-8")
    config = chosen["config"]
    labels = label_cache[(config["risk_scale"], config["horizon"])]
    cutoff = features.index[int(len(features)*.70)]
    training = [r for r in labels if r["mature_at"] < cutoff]
    testing = [r for r in labels if r["timestamp"] >= cutoff]
    predictions = fit_predict(features, training, testing, config["features"], config["model"])
    test = metrics(predictions, config["threshold"], bars)
    test["period_start"], test["period_end"] = cutoff.isoformat(), bars.index[-1].isoformat()
    observed = test["closed_trades"] >= 30 and (test["win_rate"] or 0) > 80
    rigorous = bool(observed and test["closed_trades"] >= 100
                    and test["win_rate_interval"][0] > 80 and test["double_cost_net_r"] > 0
                    and supported(chosen) and all(f["total_r"] > 0 for f in chosen["folds"]))
    result = {"symbol": symbol, "version": VERSION, "configurations": len(trials),
              "walk_forward_simulations": len(trials)*len(FOLDS), "best_validation": chosen,
              "test": test, "observed_80_percent_goal": observed,
              "evidence_gate_passed": rigorous, "future_accuracy_verified": False,
              "top_validation_candidates": ranked[:10],
              "unsupported_high_rate_trials": sum(not supported(t) and (t["validation"]["win_rate"] or 0) > 80 for t in trials),
              "status": "RESEARCH_ONLY", "elapsed_seconds": round(time.monotonic()-started, 1),
              "notes": ["All targets are exactly 3R; timeouts are not target wins.",
                        "Final 30% excluded from selection; this historical dataset has already been used in development, so final results are exploratory, not a fresh prospective test.",
                        "Classifier scores are uncalibrated; Brier score covers both directions on all evaluated patterns.",
                        "Costs assume 4 bps round trip; two-times-cost stress shown. Drawdown uses closed-trade equity.",
                        "A bounded grid is exhausted once; repeated identical simulations do not create new evidence.",
                        "No model is automatically deployed by this runner."]}
    (folder / f"{symbol}_result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(f"{symbol} FINAL: {test['win_rate']}% target hits, {test['closed_trades']} trades, {test['total_r']}R; goal={observed}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Small integration run; not the full search")
    parser.add_argument("--symbols", nargs="+", choices=("NIFTY", "BANKNIFTY"), default=["NIFTY", "BANKNIFTY"])
    args = parser.parse_args()
    from threadpoolctl import threadpool_limits
    import sklearn
    root = Path(__file__).resolve().parent / "index_research_cache"
    folder = root / (datetime.now(timezone.utc).strftime("search_%Y%m%dT%H%M%S_%fZ"))
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "quick": args.quick, "sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__,
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "execution_source_sha256": hashlib.sha256(Path(__file__).with_name("index_pattern.py").read_bytes()).hexdigest(),
                "folds": FOLDS, "thresholds": THRESHOLDS, "risk_scales": RISK_SCALES,
                "horizons": HORIZONS, "models": MODEL_NAMES, "features": FEATURE_SETS, "data": {}}
    frames = {}
    for symbol in args.symbols:
        source = root / f"{symbol}_hourly.csv"
        manifest["data"][symbol] = hashlib.sha256(source.read_bytes()).hexdigest()
        frame = pd.read_csv(source, index_col=0)
        frame.index = pd.to_datetime(frame.index, utc=True)
        frames[symbol] = frame
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Experiment artifacts: {folder}", flush=True)
    with threadpool_limits(limits=2):
        results = [research(frames[symbol], symbol, folder, args.quick) for symbol in args.symbols]
    (root / "latest_search.json").write_text(json.dumps({"folder": str(folder), "results": results}, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
