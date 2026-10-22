"""Custom candle learning with a purged chronological holdout, per index.

No indicator library or fitted-on-test accuracy. Feature weights and scaling
are learned from training data; nearest neighbours retain auditable examples.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

MODEL_VERSION = "candle-analogs-v2"
HORIZON = 21  # Session bars, including the closing 15-minute segment.
NEIGHBOURS = 48
MIN_TRAIN = 240
MIN_HISTORY = 1000
MIN_TEST_TRADES = 30
ROUND_TRIP_BPS = 4.0  # Illustrative cost/slippage, not broker-specific.
FEATURES = (
    "body_pressure", "wick_rejection", "path_efficiency", "range_compression",
    "failed_upper_break", "failed_lower_break", "price_acceptance", "gap_response",
    "path_1", "path_3", "path_6", "path_12",
)


def clean_bars(data: pd.DataFrame, now=None) -> pd.DataFrame:
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise ValueError("Hourly data must have timezone-aware timestamps")
    data = data.copy().sort_index()
    data = data.loc[~data.index.duplicated(keep="last")]
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    local = data.index.tz_convert("Asia/Kolkata")
    minutes = local.hour * 60 + local.minute
    duration = pd.to_timedelta(np.where(minutes == 915, 15, 60), unit="m")
    data = data.loc[(minutes >= 555) & (minutes <= 915) & (local.minute == 15)
                    & (local.dayofweek < 5) & (data.index + duration <= current)]
    columns = ["Open", "High", "Low", "Close"]
    if not all(c in data for c in columns) or data.empty:
        raise ValueError("Missing hourly prices")
    prices = data[columns].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Invalid hourly prices")
    if ((data.High < data[columns].max(axis=1)) | (data.Low > data[columns].min(axis=1))).any():
        raise ValueError("Invalid OHLC bars")
    return data


def session_gaps(bars: pd.DataFrame) -> np.ndarray:
    """Flag missing regular-session bars, without guessing holiday calendars."""
    local = bars.index.tz_convert("Asia/Kolkata")
    gaps = np.zeros(len(bars), dtype=bool)
    if len(bars):
        gaps[0] = local[0].hour != 9
    for i in range(1, len(bars)):
        previous, current = local[i-1], local[i]
        if previous.date() == current.date():
            gaps[i] = current-previous != pd.Timedelta(hours=1)
        else:
            gaps[i] = previous.hour != 15 or current.hour != 9
    return gaps


def prepare(data: pd.DataFrame, now=None) -> pd.DataFrame:
    """Causal custom chart descriptors; only completed full hours signal."""
    data = clean_bars(data, now)
    raw_index = data.index
    gap_count = np.r_[0, np.cumsum(session_gaps(data))]
    data = data.loc[data.index.tz_convert("Asia/Kolkata").hour != 15].copy()
    spread = (data.High-data.Low).replace(0, np.nan)
    body = data.Close-data.Open
    upper = data.High-data[["Open", "Close"]].max(axis=1)
    lower = data[["Open", "Close"]].min(axis=1)-data.Low
    prior_high = data.High.shift().rolling(12).max()
    prior_low = data.Low.shift().rolling(12).min()
    # Stop budget: historical 65th percentile of three-hour price envelopes.
    envelope = data.High.rolling(3).max()-data.Low.rolling(3).min()
    data["risk_points"] = envelope.rolling(24).quantile(.65).clip(lower=data.Close*.001).round(2)
    data["body_pressure"] = body.rolling(6).sum()/spread.rolling(6).sum()
    data["wick_rejection"] = (lower-upper).rolling(6).sum()/spread.rolling(6).sum()
    data["path_efficiency"] = data.Close.diff(6)/data.Close.diff().abs().rolling(6).sum().replace(0, np.nan)
    data["range_compression"] = spread.rolling(3).mean()/spread.rolling(12).mean()-1
    data["failed_upper_break"] = ((data.High > prior_high) & (data.Close < prior_high)).rolling(3).mean()
    data["failed_lower_break"] = ((data.Low < prior_low) & (data.Close > prior_low)).rolling(3).mean()
    data["price_acceptance"] = ((data.Close-(prior_high+prior_low)/2)/(prior_high-prior_low).replace(0, np.nan)).rolling(3).mean()
    data["gap_response"] = ((data.Open-data.Close.shift())*np.sign(body)/data.risk_points).rolling(3).mean()
    for length in (1, 3, 6, 12):
        data[f"path_{length}"] = data.Close.diff(length)/data.risk_points
    # A discontinuous history is not a valid hourly chart pattern. Resume only
    # after the longest descriptor's lookback has cleared the missing candles.
    raw_positions = raw_index.get_indexer(data.index)
    for i, position in enumerate(raw_positions):
        lookback = raw_positions[max(0, i-26)]
        if gap_count[position+1]-gap_count[lookback]:
            data.iloc[i, data.columns.get_indexer(list(FEATURES))] = np.nan
    return data.replace([np.inf, -np.inf], np.nan)


def levels(entry: float, risk_points: float, direction: int) -> dict:
    entry, risk = round(float(entry), 2), round(float(risk_points), 2)
    if direction not in (-1, 1) or risk <= 0:
        raise ValueError("A positive stop budget and LONG/SHORT direction are required")
    return {"entry": entry, "stop": round(entry-direction*risk, 2),
            "target": round(entry+direction*3*risk, 2), "risk_points": risk}


def outcome(bars: pd.DataFrame, start: int, risk: float, direction: int, *, prices=None, horizon=HORIZON) -> dict | None:
    """Next-open trade; closing segment participates in every fill/exit."""
    if horizon < 1 or start < 0:
        raise ValueError("Invalid trade horizon or start")
    if start+horizon > len(bars):
        return None  # Uniform label maturation, including early exits.
    if prices is None:
        prices = bars[["Open", "High", "Low", "Close"]].to_numpy(float)
    p = levels(prices[start, 0], risk, direction)
    long = direction == 1
    exit_index = start+horizon-1
    exit_price, kind = float(prices[exit_index, 3]), "TIMEOUT"
    for j in range(start, start+horizon):
        opened, high, low, _ = prices[j]
        # Open precedes intrabar range: honour target gaps before a reversal.
        if (opened <= p["stop"] if long else opened >= p["stop"]):
            exit_index, exit_price, kind = j, float(opened), "STOP"
            break
        if (opened >= p["target"] if long else opened <= p["target"]):
            exit_index, exit_price, kind = j, p["target"], "TARGET"
            break
        stopped = low <= p["stop"] if long else high >= p["stop"]
        hit = high >= p["target"] if long else low <= p["target"]
        if stopped or hit:
            exit_index = j
            exit_price, kind = (p["stop"], "STOP") if stopped else (p["target"], "TARGET")
            break
    gross_r = (exit_price-p["entry"])*direction/p["risk_points"]
    cost_r = p["entry"]*ROUND_TRIP_BPS/10_000/p["risk_points"]
    return {"start": start, "end": exit_index, "mature_end": start+horizon-1,
            "kind": kind, "gross_r": gross_r, "net_r": gross_r-cost_r, **p}


def make_labels(features: pd.DataFrame, bars: pd.DataFrame) -> list[dict]:
    rows = []
    prices = bars[["Open", "High", "Low", "Close"]].to_numpy(float)
    gaps = session_gaps(bars)
    for i, (timestamp, row) in enumerate(features.iterrows()):
        if row[list(FEATURES)].isna().any() or not pd.notna(row.risk_points):
            continue
        start = int(bars.index.searchsorted(timestamp+pd.Timedelta(hours=1)))
        if gaps[start:start+HORIZON].any():
            continue
        long = outcome(bars, start, row.risk_points, 1, prices=prices)
        short = outcome(bars, start, row.risk_points, -1, prices=prices)
        if long is not None and short is not None:
            rows.append({"feature_index": i, "timestamp": timestamp,
                         "mature_at": bars.index[long["mature_end"]], "long": long, "short": short})
    return rows


class PatternModel:
    """Supervised, train-scaled historical analogs with learned feature weights."""

    def __init__(self, features: pd.DataFrame, labels: list[dict]):
        if len(labels) < MIN_TRAIN:
            raise ValueError("Insufficient matured training patterns")
        self.labels = labels
        self.positions = np.array([r["feature_index"] for r in labels])
        x = features.iloc[self.positions][list(FEATURES)].to_numpy(float)
        self.center = np.median(x, axis=0)
        self.scale = np.maximum(np.std(x, axis=0), 1e-6)
        self.x = np.clip((x-self.center)/self.scale, -6, 6)
        advantage = np.array([np.clip(r["long"]["net_r"], -3, 3)-np.clip(r["short"]["net_r"], -3, 3) for r in labels])
        centered = advantage-advantage.mean()
        denominator = self.x.std(axis=0)*max(advantage.std(), 1e-6)
        correlation = np.mean((self.x-self.x.mean(axis=0))*centered[:, None], axis=0)/np.maximum(denominator, 1e-6)
        self.weights = .25+np.abs(correlation)
        self.weights /= self.weights.sum()

    def predict(self, row: pd.Series, evidence=False) -> dict:
        query = np.clip((row[list(FEATURES)].to_numpy(float)-self.center)/self.scale, -6, 6)
        distance = np.sqrt(np.sum((self.x-query)**2*self.weights, axis=1))
        selected = []
        for candidate in np.argsort(distance):
            if all(abs(self.positions[candidate]-self.positions[old]) >= 6 for old in selected):
                selected.append(int(candidate))
            if len(selected) == NEIGHBOURS:
                break
        if len(selected) < 24:
            raise ValueError("Insufficient distinct historical pattern windows")
        scores = {}
        for side in ("long", "short"):
            outcomes = [self.labels[i][side] for i in selected]
            scores[side] = {"target_rate": float(np.mean([r["kind"] == "TARGET" for r in outcomes])),
                            "mean_net_r": float(np.mean([r["net_r"] for r in outcomes]))}
        side = max(scores, key=lambda s: (scores[s]["target_rate"], scores[s]["mean_net_r"]))
        result = {"side": side, "direction": 1 if side == "long" else -1,
                  "score": scores[side]["target_rate"], "mean_net_r": scores[side]["mean_net_r"],
                  "analog_count": len(selected), "scores": scores}
        if evidence:
            result["examples"] = [{"date": self.labels[i]["timestamp"].isoformat(),
                                   "outcome": self.labels[i][side]["kind"],
                                   "net_r": round(self.labels[i][side]["net_r"], 3)} for i in selected[:5]]
        return result


def wilson(wins: int, count: int) -> list[float] | None:
    if not count:
        return None
    p, z = wins/count, 1.96
    center = (p+z*z/(2*count))/(1+z*z/count)
    width = z*math.sqrt(p*(1-p)/count+z*z/(4*count*count))/(1+z*z/count)
    return [round(100*(center-width), 2), round(100*(center+width), 2)]


def simulate(predictions: list[tuple[dict, dict]], threshold: float, bars: pd.DataFrame) -> dict:
    trades, last_exit = [], -1
    for label, prediction in predictions:
        if prediction["score"] < threshold or prediction["mean_net_r"] <= 0:
            continue
        trade = label[prediction["side"]]
        if trade["start"] <= last_exit:
            continue
        last_exit = trade["end"]
        trades.append({"signal_at": label["timestamp"].isoformat(),
                       "entry_at": bars.index[trade["start"]].isoformat(),
                       "exit_at": bars.index[trade["end"]].isoformat(),
                       "direction": prediction["side"].upper(), "outcome": trade["kind"],
                       "net_r": round(trade["net_r"], 4), "gross_r": round(trade["gross_r"], 4),
                       "holding_bars": trade["end"]-trade["start"]+1,
                       **{key: trade[key] for key in ("entry", "stop", "target")}})
    net = [t["net_r"] for t in trades]
    wins = sum(t["outcome"] == "TARGET" for t in trades)
    equity = np.cumsum([0, *net])
    gains, losses = sum(r for r in net if r > 0), -sum(r for r in net if r < 0)
    streak = longest_streak = 0
    for r in net:
        streak = streak+1 if r < 0 else 0
        longest_streak = max(longest_streak, streak)
    costs = sum(t["gross_r"]-t["net_r"] for t in trades)
    return {"closed_trades": len(trades), "wins": wins,
            "win_rate": round(100*wins/len(trades), 2) if trades else None,
            "profitable_trades": sum(r > 0 for r in net),
            "timeouts": sum(t["outcome"] == "TIMEOUT" for t in trades),
            "total_r": round(sum(net), 3),
            "mean_r": round(float(np.mean(net)), 3) if net else None,
            "profit_factor": round(gains/losses, 3) if losses else None,
            "profit_factor_note": "No losing trades; ratio undefined" if not losses else None,
            "profitable_rate": round(100*sum(r > 0 for r in net)/len(net), 2) if net else None,
            "average_winner_r": round(gains/sum(r > 0 for r in net), 3) if gains else None,
            "average_loser_r": round(-losses/sum(r < 0 for r in net), 3) if losses else None,
            "longest_losing_streak": longest_streak,
            "cost_r": round(costs, 3), "double_cost_net_r": round(sum(net)-costs, 3),
            "average_holding_bars": round(float(np.mean([t["holding_bars"] for t in trades])), 2) if trades else None,
            "max_drawdown_r": round(float(np.max(np.maximum.accumulate(equity)-equity)), 3),
            "win_rate_interval": wilson(wins, len(trades)), "trades": trades}


def analyze(data: pd.DataFrame, now=None) -> dict:
    bars, features = clean_bars(data, now), prepare(data, now)
    if len(features) < MIN_HISTORY:
        raise ValueError(f"Need at least {MIN_HISTORY} completed hourly candles for train/validation/test")
    if features.iloc[-1][list(FEATURES)].isna().any():
        raise ValueError("Latest chart lacks valid pattern features")
    labels = make_labels(features, bars)
    train_end = features.index[int(len(features)*.50)]
    test_start = features.index[int(len(features)*.70)]
    # Entire possible label horizon must finish BEFORE the next partition.
    training = [r for r in labels if r["mature_at"] < train_end]
    validation = [r for r in labels if train_end <= r["timestamp"] < test_start and r["mature_at"] < test_start]
    development = [r for r in labels if r["mature_at"] < test_start]
    testing = [r for r in labels if r["timestamp"] >= test_start]
    validation_model = PatternModel(features, training)
    predictions = [(r, validation_model.predict(features.iloc[r["feature_index"]])) for r in validation]
    trials = [(t, simulate(predictions, t, bars)) for t in (.20, .25, .30, .35, .40, .45, .50, .60, .70, .80)]
    supported = [(t, s) for t, s in trials if s["closed_trades"] >= 12]
    # Select using validation alone, including losing experiments. Never hide
    # a failed model behind an arbitrarily high threshold with zero test trades.
    threshold, selection = max(supported, key=lambda item: item[1]["mean_r"]) if supported else trials[0]
    validation_passed = bool(supported) and selection["total_r"] > 0
    test_model = PatternModel(features, development)
    test_predictions = [(r, test_model.predict(features.iloc[r["feature_index"]])) for r in testing]
    report = simulate(test_predictions, threshold, bars)
    report.update({"period_start": test_start.isoformat(), "period_end": bars.index[-1].isoformat(),
                   "accuracy_verified": False, "cost_bps_round_trip": ROUND_TRIP_BPS,
                   "excluded_unmatured_patterns": int(sum(features.index > labels[-1]["timestamp"])),
                   "data_gap_boundaries": int(session_gaps(bars).sum()),
                   "method": "Final 30% chronological holdout; frozen pre-test model and validation-selected threshold. Target-before-stop rate; timeouts are non-wins; next-session-bar open; stop-first intrabar ambiguity; 21-session-bar time exit."})
    evidence_ok = validation_passed and report["closed_trades"] >= MIN_TEST_TRADES and report["total_r"] > 0
    # Use the SAME frozen model used in the holdout for the current signal.
    latest = test_model.predict(features.iloc[-1], evidence=True)
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    stale = current-features.index[-1]-pd.Timedelta(hours=1) > pd.Timedelta(hours=2)
    passes = latest["score"] >= threshold and latest["mean_net_r"] > 0
    status = "STALE" if stale else "RESEARCH_ONLY" if not evidence_ok else latest["side"].upper() if passes else "WAIT"
    weights = sorted(zip(FEATURES, test_model.weights), key=lambda item: -item[1])
    return {"status": status, "as_of": features.index[-1].isoformat(), "bars": len(features),
            "setup": levels(features.iloc[-1].Close, features.iloc[-1].risk_points, latest["direction"]) if status in ("LONG", "SHORT") else None,
            "reason": "Custom candle-pattern model. Reference entry uses the completed close; recompute the same 1:3 levels at the actual fill. " +
                      ("Holdout evidence gate passed; future accuracy remains unknown." if evidence_ok else "Research only: validation must be positive with 12 trades, and holdout must be positive with at least 30 trades before signals are enabled."),
            "backtest": report,
            "model": {"version": MODEL_VERSION, "name": "Learned candle analogs", "threshold": threshold,
                      "training_patterns": len(training), "development_patterns": len(development),
                      "validation_trades": selection["closed_trades"], "validation_net_r": selection["total_r"],
                      "validation_passed": validation_passed, "evidence_gate_passed": evidence_ok,
                      "validation_trials": [{"threshold": t, "trades": s["closed_trades"], "net_r": s["total_r"], "mean_r": s["mean_r"]} for t, s in trials],
                      "train_end": train_end.isoformat(), "test_start": test_start.isoformat(),
                      "goal_met_in_holdout": report["closed_trades"] >= MIN_TEST_TRADES and (report["win_rate"] or 0) > 80,
                      "score_note": "Analog target-hit frequency, not a calibrated probability. Historical windows can remain correlated. Test performance is an estimate, not a guarantee.",
                      "latest": latest,
                      "feature_weights": [{"name": name, "weight": round(float(weight), 4),
                                           "value": round(float(features.iloc[-1][name]), 4)} for name, weight in weights]}}
