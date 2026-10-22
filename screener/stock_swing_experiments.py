"""Offline entry/exit experiments on cached real bars; never opens the paper DB."""
from __future__ import annotations

from collections import Counter
import copy
from datetime import date
import gzip
import hashlib
import json
import math
from pathlib import Path

from screener.stock_paper import (
    FEE, SLIPPAGE, INITIAL_CAPITAL, advance, evaluate_stock, event,
    new_account, queue_setups, week,
)
from screener.stock_paper_backtest import HistoricalView, OUTPUT as SOURCE, statistics

OUTPUT = SOURCE.parent / "stock_swing_experiments"
ENTRIES = ("breakout", "pullback", "retest")
EXITS = ("original_10", "target8_20", "partial5_trail_30")


def alternative(member, bars, benchmark, kind):
    """Confirmed after-close entries using only the visible price prefix."""
    if len(bars) < 200 or len(benchmark) < 200 or bars[-1]["date"] != benchmark[-1]["date"]:
        return None
    latest = bars[-1]
    close = latest["close"]
    closes = [b["close"] for b in bars]
    mean20 = sum(closes[-20:]) / 20
    mean50 = sum(closes[-50:]) / 50
    mean200 = sum(closes[-200:]) / 200
    if benchmark[-1]["close"] <= sum(b["close"] for b in benchmark[-50:]) / 50:
        return None
    if close < 50 or not close > mean50 > mean200:
        return None
    if sum(b["volume"] * b["close"] for b in bars[-20:]) / 20 < 100_000_000:
        return None
    by_day = {b["date"]: b for b in bars}
    start = benchmark[-21]["date"]
    if start not in by_day:
        return None
    relative = (close / by_day[start]["close"] - benchmark[-1]["close"] / benchmark[-21]["close"]) * 100
    average_volume = sum(b["volume"] for b in bars[-21:-1]) / 20
    ratio = latest["volume"] / average_volume if average_volume else 0
    strength = (close - latest["low"]) / max(latest["high"] - latest["low"], 0.001)
    atr = sum(max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
              for p, b in zip(bars[-15:-1], bars[-14:])) / 14
    if relative < 2 or ratio < 1 or strength < 0.7 or atr <= 0:
        return None
    if kind == "pullback":
        # Today's close must confirm a rebound beyond yesterday's high, after
        # yesterday touched its then-known 20-session mean from above.
        previous_mean = sum(closes[-21:-1]) / 20
        valid = (bars[-2]["low"] <= previous_mean * 1.01
                 and bars[-2]["close"] >= previous_mean * 0.98
                 and bars[-3]["close"] > previous_mean
                 and close > bars[-2]["high"] and close > mean20
                 and close <= mean20 + 1.5 * atr)
    elif kind == "retest":
        valid = False
        # A breakout 2..10 sessions ago, with its own preceding 20-bar level.
        for offset in range(2, 11):
            pos = len(bars) - 1 - offset
            level = max(b["high"] for b in bars[pos-20:pos])
            breakout = bars[pos]
            intervening = bars[pos+1:]
            if (breakout["close"] > level and bars[pos-1]["close"] <= level
                    and min(b["low"] for b in intervening) <= level * 1.01
                    and min(b["close"] for b in intervening) >= level * 0.98
                    and close > level and close > bars[-2]["high"]
                    and close - level <= atr):
                valid = True
                break
    else:
        raise ValueError(kind)
    if not valid:
        return None
    stop = min(b["low"] for b in bars[-5:]) - 0.1 * atr
    risk = close - stop
    if not 0.02 <= risk / close <= 0.08:
        return None
    return dict(symbol=member["symbol"], company=member.get("name", member["symbol"]),
                sector=member.get("industry", "Unknown"), status="QUALIFIED", as_of=latest["date"],
                reason=f"Confirmed {kind}; next-session open only", close=close,
                score=round(min(100, 40 + min(20, relative * 2) + min(20, (ratio - 1) * 15) + 20 * strength), 1),
                reference=round(close, 2), stop=round(stop, 2), target=round(close + 3 * risk, 2),
                max_entry=round(close + 0.5 * risk, 2))


def research_exit(style):
    if style == "original_10":
        return None
    if style not in EXITS:
        raise ValueError(style)

    def handle(account, trade, bar, day):
        def sell(quantity, price, kind, final):
            fill = price * (1 - SLIPPAGE)
            proceeds = quantity * fill * (1 - FEE)
            account["cash"] += proceeds
            trade.setdefault("exit_legs", []).append(dict(date=day, quantity=quantity, price=fill, proceeds=proceeds, kind=kind))
            if final:
                trade.update(status=kind, exit=fill, exit_date=day,
                             net_pnl=proceeds + trade.get("partial_proceeds", 0) + trade["dividends"] - trade["cost"])
            else:
                trade["quantity"] -= quantity
                trade["partial_proceeds"] = trade.get("partial_proceeds", 0) + proceeds
                trade["partial_taken"] = True
            event(account, day, trade["symbol"], kind, f"Research exit: {quantity} shares at {fill:.4f}")

        # Conservative ambiguity: an existing stop wins over any profit level.
        if bar["low"] <= trade["stop"]:
            sell(trade["quantity"], min(bar["open"], trade["stop"]), "STOP", True)
            return
        if style == "partial5_trail_30" and not trade.get("partial_taken"):
            partial_level = trade["entry"] * 1.05
            if bar["high"] >= partial_level:
                quantity = math.floor(trade["quantity"] / 2)
                if quantity > 0:
                    sell(quantity, partial_level, "PARTIAL_5", False)
        if bar["high"] >= trade["target"]:
            sell(trade["quantity"], trade["target"], "TARGET", True)
            return
        limit = 20 if style == "target8_20" else 30
        if trade["sessions"] >= limit:
            sell(trade["quantity"], bar["close"], "TIME_EXIT", True)
            return
        if trade.get("partial_taken"):
            trade["trail_peak"] = max(trade.get("trail_peak", trade["entry"]), bar["close"])
            # Raised only AFTER evaluating today's range: effective next day.
            trade["stop"] = max(trade["stop"], trade["entry"], trade["trail_peak"] * 0.97)
    return handle


def prepare(dataset, sessions):
    view = HistoricalView(dataset["frames"])
    execution, signals = {}, {kind: {} for kind in ENTRIES}
    for i, day in enumerate(sessions):
        visible = view.at(day)
        execution[day] = {s: [b[-1]] for s, b in visible.items() if b and b[-1]["date"] == day}
        for kind in ENTRIES:
            signals[kind][day] = []
        benchmark = visible["^NSEI"]
        for member in dataset["members"]:
            bars = visible.get(member["symbol"], [])
            candidate = evaluate_stock(member["symbol"], member.get("name", member["symbol"]),
                                       member.get("industry", "Unknown"), bars, benchmark)
            if candidate["status"] == "QUALIFIED":
                signals["breakout"][day].append(candidate)
            for kind in ("pullback", "retest"):
                candidate = alternative(member, bars, benchmark, kind)
                if candidate:
                    signals[kind][day].append(candidate)
        if i % 75 == 0:
            print(f"Signals {i+1}/{len(sessions)}", flush=True)
    return execution, signals


def replay(execution, signals, benchmark, days, entry, exits):
    account = new_account()
    handler = research_exit(exits)
    for day in days:
        advance(account, execution[day], [day], exit_handler=handler)
        if account["warnings"]:
            raise ValueError(account["warnings"])
        candidates = copy.deepcopy(signals[entry][day])
        for candidate in candidates:
            if exits != "original_10":
                target_pct = 0.08 if exits == "target8_20" else 0.10
                candidate["target"] = round(candidate["reference"] * (1 + target_pct), 2)
                # Final target must have >=2R even before the opening fill.
                # Partial exits reduce the blended reward; no 2R promise.
                if (candidate["target"] - candidate["reference"]) / (candidate["reference"] - candidate["stop"]) < 2:
                    candidate["status"] = "REJECTED"
        candidates.sort(key=lambda c: (-c["score"], c["symbol"]))
        queue_setups(account, candidates, day, day)
        assert account["cash"] >= -0.000001
        assert sum(t["status"] == "OPEN" for t in account["trades"]) <= 4
    assert max(Counter(week(t["entry_date"]) for t in account["trades"]).values(), default=0) <= 6
    result = statistics(account, days[0], days[-1], [b for b in benchmark if days[0] <= b["date"] <= days[-1]])
    closed = [t for t in account["trades"] if t["status"] != "OPEN"]
    result["closed_trades_net_at_least_5pct"] = sum(t["net_pnl"] / t["cost"] >= 0.05 for t in closed)
    result["closed_trades_net_at_least_10pct"] = sum(t["net_pnl"] / t["cost"] >= 0.10 for t in closed)
    result["partial_profit_trades"] = sum(bool(t.get("partial_taken")) for t in account["trades"])
    result["max_weekly_entries"] = max(Counter(week(t["entry_date"]) for t in account["trades"]).values(), default=0)
    result["events"] = account["events"]
    # Reconcile whole-account wealth, including unsold shares and partial exits.
    unrealized = sum(t["quantity"] * t["mark"] + t.get("partial_proceeds", 0) + t["dividends"] - t["cost"]
                     for t in account["trades"] if t["status"] == "OPEN")
    assert abs(result["net_change"] - result["realized_pnl"] - unrealized) < 1e-6
    return result


def objective(result):
    return result["return_pct"] - 0.5 * result["max_drawdown_pct"] if result["closed_trades"] >= 30 else -1e9


def write_json(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = SOURCE / "source_history.json.gz"
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        dataset = json.load(stream)
    benchmark = dataset["frames"]["^NSEI"]
    end = date.fromisoformat(benchmark[-1]["date"])
    start = end.replace(year=end.year-2).isoformat()
    sessions = [b["date"] for b in benchmark if b["date"] >= start]
    if len(sessions) < 400 or sum(b["date"] < start for b in benchmark) < 200:
        raise ValueError("Need two years of sessions plus 200 warmup bars")
    earlier, later = sessions[:-126], sessions[-126:]
    protocol = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    engine_sha256=hashlib.sha256((Path(__file__).parent / "stock_paper.py").read_bytes()).hexdigest(),
                    earlier=[earlier[0], earlier[-1]], later=[later[0], later[-1]],
                    variants=[f"{a}/{b}" for a in ENTRIES for b in EXITS],
                    selection="Earlier return minus 0.5 * daily-close drawdown, minimum 30 closed trades",
                    promotion="Research only; no automatic activation. This dataset was already examined. Forward paper evidence required.")
    write_json("protocol.json", protocol)
    execution, signals = prepare(dataset, sessions)
    results = {}
    for entry in ENTRIES:
        for exits in EXITS:
            key = f"{entry}/{exits}"
            results[key] = replay(execution, signals, benchmark, earlier, entry, exits)
            r = results[key]
            print(f"Earlier {key}: {r['return_pct']:.2f}%, DD {r['max_drawdown_pct']:.2f}%, {r['closed_trades']} trades", flush=True)
    selected = max(results, key=lambda key: objective(results[key]))
    if objective(results[selected]) == -1e9:
        selected = "breakout/original_10"
    write_json("frozen_selection.json", dict(selected=selected, objective=objective(results[selected]), protocol=protocol))
    baseline = replay(execution, signals, benchmark, later, "breakout", "original_10")
    candidate = replay(execution, signals, benchmark, later, *selected.split("/"))
    # Exact parity with the previously saved unmodified production replay.
    full_baseline = replay(execution, signals, benchmark, sessions, "breakout", "original_10")
    original = json.loads((SOURCE / "2y.json").read_text(encoding="utf-8"))
    assert full_baseline["trades"] == original["trades"], "Baseline trade parity failed"
    assert full_baseline["equity_curve"] == original["equity_curve"], "Baseline equity parity failed"
    full_selected = replay(execution, signals, benchmark, sessions, *selected.split("/"))
    report = dict(protocol=protocol, selected=selected, decision="NOT_ACTIVATED", earlier=results,
                  later_baseline=baseline, later_selected=candidate,
                  full_baseline=full_baseline, full_selected=full_selected, baseline_parity=True,
                  data_fetched_at=dataset["fetched_at"], universe_source=dataset["universe_source"],
                  unavailable_symbols=[m["symbol"] for m in dataset["members"] if not dataset["frames"].get(m["symbol"])])
    write_json("results.json", report)
    lines = ["# Stock swing entry and exit experiments", "", f"Selected on earlier period: **{selected}**. Decision: **NOT ACTIVATED**.", "",
             f"Earlier selection: {earlier[0]} to {earlier[-1]}. Later comparison: {later[0]} to {later[-1]}.", "",
             "Independent INR 100,000 accounts. Net of assumed 0.10% costs and 0.05% adverse slippage per side.", "",
             "## Earlier selection period", "", "| Entry / exit | Return | Drawdown | Closed trades | Objective |", "|---|---:|---:|---:|---:|"]
    for key, r in results.items():
        score = f"{objective(r):.2f}" if r["closed_trades"] >= 30 else "Ineligible (<30 trades)"
        lines.append(f"| {key} | {r['return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['closed_trades']} | {score} |")
    for title, base, chosen in (("Later comparison", baseline, candidate), ("Full two years (descriptive, includes selection data)", full_baseline, full_selected)):
        lines += ["", f"## {title}", "", "| Metric | Original | Selected |", "|---|---:|---:|"]
        for label, key in (("Return (%)", "return_pct"), ("Ending marked equity (INR)", "ending_equity"),
                           ("Estimated liquidation equity (INR)", "estimated_liquidation_equity"),
                           ("Drawdown (%)", "max_drawdown_pct"), ("Closed trades", "closed_trades"),
                           ("Win rate (%)", "win_rate_pct"), ("Profit factor", "profit_factor"),
                           ("Closed trades net >=5% of entry cost", "closed_trades_net_at_least_5pct"),
                           ("Closed trades net >=10% of entry cost", "closed_trades_net_at_least_10pct"),
                           ("Full months >=5%", "months_at_least_5pct"), ("Full months", "complete_months"),
                           ("Open positions", "open_positions"), ("Maximum weekly entries", "max_weekly_entries")):
            def fmt(value):
                return f"{value:.2f}" if isinstance(value, float) else str(value)
            lines.append(f"| {label} | {fmt(base[key])} | {fmt(chosen[key])} |")
    lines += ["", "## Selected strategy monthly history (full two years)", "",
              "| Month | Return | Entries | Coverage |", "|---|---:|---:|---|"]
    for month in full_selected["monthly"]:
        lines.append(f"| {month['month']} | {month['return_pct']:.2f}% | {month['entries']} | {'Partial' if month['partial'] else 'Full'} |")
    lines += ["", "## Interpretation and limitations", "",
              "- These are exploratory results. The source period was already examined in previous strategy and AI research; the later period is not wholly untouched independent evidence.",
              "- Frozen selection was saved before this run's later comparison. Nine variants only; later results never choose the variant.",
              "- Today's Nifty 500 membership and sectors are used historically (survivorship bias). Yahoo source data can be revised; historical membership and other corporate reorganizations are not reconstructed.",
              "- Confirmed close signals enter only at the next open within the existing gap band. No same-close or invented intraday trigger fills. Missing required bars abort the run.",
              "- Cash, sector, four-position, six-entry weekly and planned INR 750/0.75% risk caps use the production execution engine. Gaps can exceed planned risk. Baseline trades and equity exactly match the saved two-year production replay.",
              "- Original exit is 3R/10 sessions. Target8 uses 8% above signal close/20 sessions. Partial5 sells half the shares at 5% above actual fill, targets 10% above signal close, and trails the remaining shares 3% below the best close after partial profit, with a gross-entry floor and 30-session limit.",
              "- A raised trailing stop is effective next session. Stop-first on ambiguous daily ranges, including partial-profit days. Half-share quantity rounds down. Partial sale proceeds cannot fund entries earlier the same day.",
              "- Fixed profit targets still require >=2R to the final target at signal and fill. The partial exit reduces blended reward. Gross 5%/10% levels do not imply net 5%/10% trade returns.",
              "- Splits adjust quantities and price levels; dividends credited on ex-date. Trades retain partial-exit legs. Equity and profit are reconciled including partial proceeds and open holdings.",
              "- Open positions are marked at the end; liquidation estimate includes remaining exit costs. Boundary months excluded from goal counts. Daily-close drawdown understates possible intraday drawdown.",
              "- No model or strategy is connected to the running paper scanner by this experiment. Forward paper results are required before claiming an improvement or return target.", "",
              "Complete trades, orders, partial exit legs, monthly history and equity curves: [results.json](./results.json).",
              "Rules and rerun instructions: [research documentation](../../../docs/STOCK_SWING_EXPERIMENTS.md)."]
    (OUTPUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(dict(selected=selected, later={k: candidate[k] for k in ("return_pct", "max_drawdown_pct", "closed_trades", "months_at_least_5pct")}, parity=True)), flush=True)


if __name__ == "__main__":
    main()
