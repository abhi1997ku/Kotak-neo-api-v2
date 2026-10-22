"""Offline, chronological model selection for stock paper setup ranking.

No broker calls. No writes to the paper account. Model promotion is gated by
the validation and later walk-forward results, never by training performance.
"""
from __future__ import annotations

from collections import defaultdict
import copy
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor

from screener.stock_paper import (
    IST, INITIAL_CAPITAL, advance, evaluate_stock, new_account, queue_setups,
)
from screener.stock_paper_backtest import HistoricalView, OUTPUT as SOURCE, statistics

OUTPUT = SOURCE.parent / "stock_ai_research"
FEATURES = ["return_5", "return_20", "return_60", "distance_sma20", "distance_sma50",
            "distance_sma200", "atr_pct", "relative_volume", "close_strength",
            "relative_strength20", "risk_pct", "breakout_extension", "benchmark_return20",
            "benchmark_distance50", "benchmark_volatility20", "baseline_score"]
THRESHOLDS = [0.0, 0.15]
SEED = 42


def features(bars, benchmark, candidate):
    closes = np.array([b["close"] for b in bars], dtype=float)
    close = closes[-1]
    last = bars[-1]
    bc = np.array([b["close"] for b in benchmark], dtype=float)
    atr = np.mean([max(b["high"]-b["low"], abs(b["high"]-p["close"]), abs(b["low"]-p["close"]))
                   for p, b in zip(bars[-15:-1], bars[-14:])])
    values = [closes[-1]/closes[-n-1]-1 for n in (5, 20, 60)]
    values += [close/np.mean(closes[-n:])-1 for n in (20, 50, 200)]
    values += [atr/close, candidate["relative_volume"],
               (close-last["low"])/max(last["high"]-last["low"], 0.001),
               candidate["relative_strength"]/100, (close-candidate["stop"])/close,
               (close-max(b["high"] for b in bars[-21:-1]))/atr,
               bc[-1]/bc[-21]-1, bc[-1]/np.mean(bc[-50:])-1,
               float(np.std(np.diff(bc[-21:])/bc[-21:-1])), candidate["score"]/100]
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite model input")
    return [float(value) for value in values]


def prepare(dataset):
    """Create causal features and separate future labels for qualified setups."""
    view = HistoricalView(dataset["frames"])
    benchmark = dataset["frames"]["^NSEI"]
    sessions = [b["date"] for b in benchmark][199:]
    candidates = defaultdict(list)
    execution = {}
    records = []
    for i, day in enumerate(sessions):
        visible = view.at(day)
        execution[day] = {symbol: [bars[-1]] for symbol, bars in visible.items() if bars and bars[-1]["date"] == day}
        for member in dataset["members"]:
            symbol = member["symbol"]
            bars = visible.get(symbol, [])
            candidate = evaluate_stock(symbol, member.get("name", symbol), member.get("industry", "Unknown"), bars, visible["^NSEI"])
            if candidate["status"] != "QUALIFIED":
                continue
            item = {"id": f"{symbol}:{day}", "day": day, "candidate": candidate,
                    "x": features(bars, visible["^NSEI"], candidate)}
            candidates[day].append(item)
            records.append(item)
        if i % 100 == 0:
            print(f"Features: {i+1}/{len(sessions)} days; {len(records)} qualified setups", flush=True)
    indexes = {day: i for i, day in enumerate(sessions)}
    for record in records:
        i = indexes[record["day"]]
        future = sessions[i+1:i+11]
        record.update(y=None, label_end=None)
        if len(future) != 10:
            continue
        account = new_account()
        account["last_session"] = record["day"]
        queue_setups(account, [copy.deepcopy(record["candidate"])], record["day"], record["day"])
        for day in future:
            advance(account, execution[day], [day])
            if account["warnings"]:
                break
        if account["warnings"]:
            continue
        # Skipped entry is a zero-return opportunity, not a successful trade.
        trade = account["trades"][0] if account["trades"] else None
        if trade and trade["status"] == "OPEN":
            raise AssertionError("Unresolved label after the ten-session horizon")
        record["y"] = trade["net_pnl"] / trade["planned_risk"] if trade else 0.0
        record["label_end"] = future[-1]
    return {"sessions": sessions, "records": records, "execution": execution,
            "benchmark": benchmark}


def make_model(name):
    if name == "extra_trees":
        return ExtraTreesRegressor(n_estimators=160, max_depth=5, min_samples_leaf=30,
                                   max_features=0.8, random_state=SEED, n_jobs=2)
    if name == "hist_gradient_boosting":
        return HistGradientBoostingRegressor(max_iter=100, max_leaf_nodes=7,
                                            learning_rate=0.04, min_samples_leaf=30,
                                            l2_regularization=10, early_stopping=False,
                                            random_state=SEED)
    raise ValueError(name)


def eligible_training(records, cutoff):
    # Purge overlapping labels across ALL symbols. No shuffled date split.
    return [r for r in records if r["y"] is not None and r["label_end"] < cutoff and r["day"] < cutoff]


def predict_forward(prepared, days, name):
    """Refit monthly using only completed past outcomes; freeze hyperparameters."""
    predictions, audit = {}, []
    for month in sorted({day[:7] for day in days}):
        month_days = [day for day in days if day[:7] == month]
        cutoff = month_days[0]
        train = eligible_training(prepared["records"], cutoff)
        test = [r for r in prepared["records"] if r["day"] in month_days]
        if len(train) < 100 or len({r["day"] for r in train}) < 20:
            audit.append({"cutoff": cutoff, "samples": len(train), "status": "INSUFFICIENT_TRAINING"})
            continue
        model = make_model(name)
        model.fit(np.array([r["x"] for r in train]), np.array([r["y"] for r in train]))
        if test:
            predictions.update({r["id"]: float(p) for r, p in zip(test, model.predict(np.array([r["x"] for r in test])))})
        audit.append({"cutoff": cutoff, "samples": len(train), "train_last_label": max(r["label_end"] for r in train),
                      "predicted_setups": len(test), "status": "FIT"})
    return predictions, audit


def simulate(prepared, days, predictions=None, threshold=0.0):
    by_day = defaultdict(list)
    for record in prepared["records"]:
        by_day[record["day"]].append(record)
    account = new_account()
    account["last_session"] = days[0]
    account["equity_history"].append({"date": days[0], "equity": INITIAL_CAPITAL, "cash": INITIAL_CAPITAL})
    for day in days:
        advance(account, prepared["execution"][day], [day])
        if account["warnings"]:
            raise ValueError(account["warnings"])
        candidates = []
        for record in by_day[day]:
            candidate = copy.deepcopy(record["candidate"])
            if predictions is not None:
                prediction = predictions.get(record["id"])
                if prediction is None or prediction < threshold:
                    continue
                candidate.update(score=prediction * 100, ai_expected_net_r=prediction)
            candidates.append(candidate)
        candidates.sort(key=lambda c: (-c["score"], c["symbol"]))
        queue_setups(account, candidates, day, day)
    benchmark = [b for b in prepared["benchmark"] if days[0] <= b["date"] <= days[-1]]
    return statistics(account, days[0], days[-1], benchmark)


def validation_objective(result):
    if result["closed_trades"] < 15:
        return -1_000_000.0
    return result["return_pct"] - 0.5 * result["max_drawdown_pct"]


def improvement_interval(baseline, challenger):
    """Paired moving-block bootstrap, ten-session blocks; approximate only."""
    base = np.array([r["equity"] for r in baseline["equity_curve"]])
    model = np.array([r["equity"] for r in challenger["equity_curve"]])
    changes = np.diff(model)/model[:-1] - np.diff(base)/base[:-1]
    rng = np.random.default_rng(SEED)
    samples = []
    n = len(changes)
    for _ in range(2000):
        indexes = np.concatenate([(np.arange(10) + i) % n for i in rng.integers(0, n, size=(n+9)//10)])[:n]
        samples.append(float(np.mean(changes[indexes]) * n * 100))
    return {"lower_pct_points": float(np.quantile(samples, 0.025)),
            "upper_pct_points": float(np.quantile(samples, 0.975)), "block_sessions": 10,
            "resamples": 2000, "note": "Approximate paired daily-return difference; not a guarantee or independent market sample."}


def compact(result):
    return {k: v for k, v in result.items() if k not in {"equity_curve", "trades", "orders"}}


def markdown(report):
    lines = ["# AI stock strategy research", "", f"Generated {report['generated_at']}", "",
             f"Decision: **{report['decision']}**", "",
             f"Validation: {report['validation_start']} to {report['validation_end']}. Later walk-forward test: {report['test_start']} to {report['test_end']}.", "",
             f"Qualified examples: {report['examples']}; labelled examples: {report['labelled_examples']}.", "",
             "## Validation model selection", "", "| Model | Threshold (predicted net R) | Return | Drawdown | Closed trades | Objective |", "|---|---:|---:|---:|---:|---:|"]
    for trial in report["validation_trials"]:
        r = trial["result"]
        lines.append(f"| {trial['model']} | {trial['threshold']} | {r['return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['closed_trades']} | {trial['objective']:.2f} |")
    lines += ["", f"Frozen selection: **{report['selected']['model']}**, minimum predicted net R **{report['selected']['threshold']}**.", "",
              "## Later test — after simulated costs", "", "| Metric | Original strategy | Selected AI strategy |", "|---|---:|---:|"]
    a, b = report["baseline_test"], report["challenger_test"]
    for title, key, suffix in [("Return", "return_pct", "%"), ("Ending equity (INR)", "ending_equity", ""),
                               ("Maximum drawdown", "max_drawdown_pct", "%"), ("Closed trades", "closed_trades", ""),
                               ("Win rate", "win_rate_pct", "%"), ("Profit factor", "profit_factor", ""),
                               ("Full months reaching 5%", "months_at_least_5pct", "")]:
        def fmt(value):
            return "N/A" if value is None else f"{value:,.2f}{suffix}"
        lines.append(f"| {title} | {fmt(a[key])} | {fmt(b[key])} |")
    lines += ["", "## Promotion checks", ""]
    lines += [f"- {'PASS' if value else 'FAIL'}: {name}" for name, value in report["promotion_checks"].items()]
    ci = report["improvement_interval"]
    lines += ["", f"Approximate 95% block-bootstrap difference interval: {ci['lower_pct_points']:.2f} to {ci['upper_pct_points']:.2f} percentage points.", "",
              "## Method and limitations", ""] + [f"- {note}" for note in report["limitations"]]
    lines += ["", "[Full results and audit](./results.json)", "",
              "[Chronological validation reference](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)"]
    return "\n".join(lines) + "\n"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = SOURCE / "source_history.json.gz"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    strategy_hash = hashlib.sha256(Path(__file__).with_name("stock_paper.py").read_bytes()).hexdigest()
    research_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    prepared_path = OUTPUT / "prepared.json.gz"
    key = source_hash + strategy_hash + research_hash
    if prepared_path.exists():
        with gzip.open(prepared_path, "rt", encoding="utf-8") as f:
            saved = json.load(f)
    else:
        saved = {}
    if saved.get("key") == key:
        prepared = saved["data"]
    else:
        with gzip.open(source, "rt", encoding="utf-8") as f:
            dataset = json.load(f)
        prepared = prepare(dataset)
        with gzip.open(prepared_path, "wt", encoding="utf-8") as f:
            json.dump({"key": key, "data": prepared}, f, allow_nan=False)
    days = prepared["sessions"]
    if len(days) < 400:
        raise ValueError("Need at least 400 post-warmup sessions")
    validation, test = days[-252:-126], days[-126:]
    # Before comparing AI, prove this faster research path reproduces the
    # production baseline already reported on the exact same source history.
    reference = json.loads((SOURCE / "1y.json").read_text(encoding="utf-8"))
    parity = simulate(prepared, [day for day in days if reference["start"] <= day <= reference["end"]])
    if abs(parity["ending_equity"] - reference["ending_equity"]) > 0.01 or parity["trades"] != reference["trades"]:
        raise AssertionError("Research replay differs from the production baseline; do not compare model results")
    print("Production replay parity passed: same entries, fills and ending equity", flush=True)
    protocol = {"validation_start": validation[0], "validation_end": validation[-1],
                "test_start": test[0], "test_end": test[-1], "thresholds": THRESHOLDS,
                "objective": "Validation return minus 0.5 * drawdown; at least 15 closed trades",
                "models": ["extra_trees", "hist_gradient_boosting"],
                "promotion_rule": "Positive validation and test returns, better validation objective and test return than baseline, no worse test drawdown, >=30 closed test trades, PF>1.1, bootstrap lower bound>0",
                "source_sha256": source_hash, "strategy_sha256": strategy_hash, "research_sha256": research_hash}
    (OUTPUT / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    baseline_validation = simulate(prepared, validation)
    trials = []
    audits = {}
    for name in protocol["models"]:
        predictions, audit = predict_forward(prepared, validation, name)
        audits[name] = audit
        for threshold in THRESHOLDS:
            result = simulate(prepared, validation, predictions, threshold)
            trial = {"model": name, "threshold": threshold, "objective": validation_objective(result), "result": compact(result)}
            trials.append(trial)
            print(f"Validation {name} threshold={threshold}: return {result['return_pct']:.2f}%, trades {result['closed_trades']}", flush=True)
    selected = max(trials, key=lambda t: t["objective"])
    (OUTPUT / "frozen_selection.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
    predictions, test_audit = predict_forward(prepared, test, selected["model"])
    baseline = simulate(prepared, test)
    challenger = simulate(prepared, test, predictions, selected["threshold"])
    interval = improvement_interval(baseline, challenger)
    checks = {
        "Positive validation return": selected["result"]["return_pct"] > 0,
        "Improved validation objective": selected["objective"] > validation_objective(baseline_validation),
        "Positive later-test return": challenger["return_pct"] > 0,
        "Improved later-test return": challenger["return_pct"] > baseline["return_pct"],
        "No worse later-test drawdown": challenger["max_drawdown_pct"] <= baseline["max_drawdown_pct"],
        "At least 30 closed later-test trades": challenger["closed_trades"] >= 30,
        "Later-test profit factor above 1.1": (challenger["profit_factor"] or 0) > 1.1,
        "Positive lower confidence bound for improvement": interval["lower_pct_points"] > 0,
    }
    report = dict(protocol, generated_at=datetime.now(IST).isoformat(), examples=len(prepared["records"]),
                  labelled_examples=sum(r["y"] is not None for r in prepared["records"]), features=FEATURES,
                  validation_trials=trials, baseline_validation=compact(baseline_validation), selected=selected,
                  validation_training_audit=audits, test_training_audit=test_audit,
                  baseline_test=baseline, challenger_test=challenger,
                  improvement_interval=interval, promotion_checks=checks,
                  decision="ELIGIBLE_FOR_FORWARD_PAPER_TRIAL" if all(checks.values()) else "NOT_PROMOTED",
                  limitations=[
                      "Current constituent/sector snapshot introduces survivorship and membership bias; this is not point-in-time-universe research.",
                      "The aggregate historical performance was already inspected before this experiment. Later test data is excluded from model/threshold selection, but is not a completely untouched independent dataset.",
                      "Only existing qualified breakout setups are filtered/ranked. Stops, target, ten-session holding period, capital and weekly limits remain fixed.",
                      "Model label is isolated candidate net P&L divided by initial risk after costs. Skipped entries have zero opportunity return. Portfolio limits are applied in the evaluation, not to the labels.",
                      "Monthly expanding-window refits use only labels whose entire ten-session outcome window ended before the prediction block. No shuffled splits or automatic random early-stopping validation.",
                      "Four validation trials only; the selected model and threshold are frozen before later testing. Later test outcomes can enter subsequent monthly refits only after their labels mature.",
                      "Daily bars, 0.10% costs each side, 0.05% adverse slippage, conservative stop-first fills. Provider errors and corporate reorganizations remain limitations.",
                      "The bootstrap interval is approximate and cannot remove selection bias or establish future profitability. No 5% monthly guarantee is inferred.",
                      "Research is separate from the live paper account. This script never changes the production strategy or enables a model."])
    # Store a research artifact only, never a model consumed by the server.
    last_day = days[-1]
    train = eligible_training(prepared["records"], last_day)
    artifact = make_model(selected["model"])
    artifact.fit(np.array([r["x"] for r in train]), np.array([r["y"] for r in train]))
    joblib.dump({"model": artifact, "features": FEATURES, "threshold": selected["threshold"],
                 "trained_before": last_day, "promotion_decision": report["decision"]}, OUTPUT / "research_model.joblib")
    (OUTPUT / "results.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT / "report.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "selected": selected["model"],
                      "threshold": selected["threshold"], "baseline": compact(baseline),
                      "challenger": compact(challenger), "checks": checks}, indent=2), flush=True)


if __name__ == "__main__":
    main()
