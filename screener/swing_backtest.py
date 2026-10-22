"""Chronological, portfolio-level strategy comparison on cached real daily data."""
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import date
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np

from screener.swing_data import CACHE, ROOT, load
from screener.swing_engine import Config, STRATEGIES, costs, prepare, simulate, regime


def metrics(account, cfg):
    curve = account["equity_history"]
    closed = [t for t in account["trades"] if t["status"] != "OPEN"]
    opened = [t for t in account["trades"] if t["status"] == "OPEN"]
    pnls = [t["net_pnl"] for t in closed]
    winners, losers = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    values = np.array([100000.] + [p["equity"] for p in curve])
    returns = np.diff(values)/values[:-1]
    years = max(1, (date.fromisoformat(curve[-1]["date"])-date.fromisoformat(curve[0]["date"])).days)/365.25
    downside = np.sqrt(np.mean(np.minimum(returns, 0)**2))
    yearwise, regimes = {}, {}
    for group, field in ((yearwise, "year"), (regimes, "regime")):
        for key in sorted({p["date"][:4] if field == "year" else p["regime"] for p in curve}):
            selected = [i for i, p in enumerate(curve) if (p["date"][:4] if field == "year" else p["regime"]) == key]
            trades = [t for t in closed if (t["exit_date"][:4] if field == "year" else t["entry_regime"]) == key]
            group[key] = dict(sessions=len(selected), compounded_session_return_pct=float((np.prod(1+returns[selected])-1)*100),
                              closed_trades=len(trades), net_trade_pnl=sum(t["net_pnl"] for t in trades))
    liquidation = values[-1] - sum(t["quantity"]*t["mark"]*cfg.slippage + costs(t["quantity"]*t["mark"]*(1-cfg.slippage), "sell", cfg, curve[-1]["date"])["total"] for t in opened)
    return dict(start=curve[0]["date"], end=curve[-1]["date"], ending_equity=float(values[-1]),
        estimated_liquidation_equity=float(liquidation), return_pct=float((values[-1]/100000-1)*100),
        annualized_pct=float(((values[-1]/100000)**(1/years)-1)*100),
        max_drawdown_pct=float(np.max((np.maximum.accumulate(values)-values)/np.maximum.accumulate(values))*100),
        sharpe_zero_rf=float(np.mean(returns)/np.std(returns)*np.sqrt(252)) if np.std(returns)>0 else None,
        sortino_zero_rf=float(np.mean(returns)/downside*np.sqrt(252)) if downside>0 else None,
        filled_trades=len(account["trades"]), closed_trades=len(closed), open_positions=len(opened),
        win_rate_pct=len(winners)/len(closed)*100 if closed else None,
        expectancy_inr=float(np.mean(pnls)) if pnls else None,
        expectancy_r=float(np.mean([t["net_pnl"]/t["planned_risk"] for t in closed])) if closed else None,
        average_winner=float(np.mean(winners)) if winners else None,
        average_loser=float(np.mean(losers)) if losers else None,
        profit_factor=sum(winners)/abs(sum(losers)) if losers else None,
        average_holding_sessions=float(np.mean([t["sessions"] for t in closed])) if closed else None,
        transaction_costs=sum(t.get("entry_costs", {}).get("total", 0)+t.get("exit_costs", {}).get("total", 0) for t in account["trades"]),
        yearwise=yearwise, regimes=regimes, exit_reasons=dict(Counter(t.get("exit_reason", t["status"]) for t in closed)),
        skipped=dict(Counter(o.get("outcome_reason") for o in account["orders"] if o["status"] == "SKIPPED")))


def objective(result):
    # Net expectancy and drawdown, not win rate. Small samples are ineligible.
    if result["closed_trades"] < 15:
        return -1e6
    return result["return_pct"] - result["max_drawdown_pct"] + 2*(result["expectancy_r"] or 0)


def compact(account):
    return {key: account[key] for key in ("cash", "trades", "orders", "equity_history", "events")}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    dataset = load()
    print("Preparing causal Nifty 500 signals", flush=True)
    prepared = prepare(dataset)
    days = prepared["days"]
    if len(days) < 450:
        raise ValueError("Need >=450 sessions AFTER 250-candle warmup")
    training, validation, test = days[:-252], days[-252:-126], days[-126:]
    configs = [Config(strategy=s, exit_style=e, atr_multiple=a) for s, e, a in itertools.product(STRATEGIES, ("chandelier", "swing", "ema"), (2., 3.))]
    protocol = dict(training=[training[0], training[-1]], validation=[validation[0], validation[-1]],
        out_of_sample=[test[0], test[-1]], configs=[asdict(c) for c in configs],
        selection="Top two configs per strategy on training net-return minus drawdown plus 2*expectancy-R; minimum 15 closed trades. Select between those on validation. No OOS selection.",
        data_sha256=hashlib.sha256(CACHE.read_bytes()).hexdigest(),
        engine_sha256=hashlib.sha256(Path(__file__).with_name('swing_engine.py').read_bytes()).hexdigest(),
        limitations=["Current constituent snapshot: survivorship bias; no historical membership archive available.",
          "Previously inspected market period: later data excluded from this run's selection, but not pristine independent evidence.",
          "Daily bars and simplified corporate actions; no intraday path, demerger or delisting reconstruction.",
          "Known-event archive unavailable: event risk UNKNOWN, no points awarded. Historical events are not inferred from future announcements.",
          "Itemized delivery-tax/brokerage assumptions applied even to same-day stop exits (conservative approximation). Personal capital-gains tax excluded.",
          "Sharpe/Sortino assume zero cash return and zero risk-free rate; daily-close drawdown can understate intraday loss.",
          "Open holdings are marked, not forcibly exited. Liquidation estimates and sample counts are shown. Fold accounts start flat."])
    (ROOT / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    trials = []
    for config in configs:
        account = simulate(prepared, training, config)
        result = metrics(account, config)
        trials.append(dict(config=asdict(config), training=result))
        print(f"Train {config.strategy}/{config.exit_style}/{config.atr_multiple}: {result['return_pct']:.2f}% {result['closed_trades']} trades", flush=True)
    finalists, comparisons = {}, {}
    for strategy in STRATEGIES:
        shortlisted = sorted([t for t in trials if t["config"]["strategy"] == strategy], key=lambda t: objective(t["training"]), reverse=True)[:2]
        for trial in shortlisted:
            config = Config(**trial["config"])
            trial["validation"] = metrics(simulate(prepared, validation, config), config)
        selected = max(shortlisted, key=lambda t: objective(t["validation"]))
        finalists[strategy] = dict(config=selected["config"], training=selected["training"], validation=selected["validation"])
    (ROOT / "frozen_selection.json").write_text(json.dumps(finalists, indent=2), encoding="utf-8")
    for strategy, selected in finalists.items():
        config = Config(**selected["config"])
        account = simulate(prepared, test, config)
        result = metrics(account, config)
        full = simulate(prepared, days, config)
        # Rolling-origin walk-forward: for each 63-session test block, select
        # from this strategy's six fixed variants using ONLY the prior 126 days.
        folds = []
        for start in (len(days)-126, len(days)-63):
            past, future = days[start-126:start], days[start:start+63]
            options = []
            for candidate in configs:
                if candidate.strategy == strategy:
                    m = metrics(simulate(prepared, past, candidate), candidate)
                    options.append((objective(m), candidate))
            chosen = max(options, key=lambda x: x[0])[1]
            folds.append(dict(config=asdict(chosen), selected_using=[past[0], past[-1]],
                              result=metrics(simulate(prepared, future, chosen), chosen)))
        stress = replace(config, slippage=config.slippage*2, brokerage_rate=config.brokerage_rate*2, dp_sell_fee=config.dp_sell_fee*2)
        stress_result = metrics(simulate(prepared, test, stress), stress)
        eligible = (selected["training"]["expectancy_r"] or 0) > 0 and (selected["validation"]["expectancy_r"] or 0) > 0 and (result["expectancy_r"] or 0) > 0 and result["closed_trades"] >= 30 and result["return_pct"] > 0 and result["max_drawdown_pct"] <= 10 and stress_result["return_pct"] > 0 and all(f["result"]["return_pct"] > 0 for f in folds)
        comparisons[strategy] = dict(**selected, out_of_sample=result, full_period=metrics(full, config),
                                     walk_forward=folds, cost_stress=stress_result,
                                     recommendation="FORWARD_PAPER_CANDIDATE" if eligible else "NOT_RECOMMENDED",
                                     out_of_sample_account=compact(account), full_account=compact(full))
        print(f"OOS {strategy}: {result['return_pct']:.2f}%, expectancy {result['expectancy_r']}, {result['closed_trades']} trades", flush=True)
    report = dict(protocol=protocol, universe_source=dataset["universe_source"], data_warnings=dataset.get("warnings", []),
                  trials=trials, strategies=comparisons, regime_sessions=dict(Counter(regime(prepared["snapshots"][d]["^CRSLDX"]) for d in days)))
    (ROOT / "results.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    lines = ["# Expectancy-based swing strategy comparison", "", f"Universe: {dataset['universe_source']}. Benchmark: Nifty 500 (^CRSLDX).",
             "", f"Training {training[0]}–{training[-1]}; validation {validation[0]}–{validation[-1]}; later test {test[0]}–{test[-1]}.", "",
             "No fixed profit targets. INR 100,000 independent portfolios; 0.5% planned per-entry risk including gap allowance and costs.", "",
             "| Strategy | Selected exit / ATR | Later return | Drawdown | Expectancy R | Win rate | Trades | Recommendation |",
             "|---|---|---:|---:|---:|---:|---:|---|"]
    def fmt(v):
        return "N/A" if v is None else f"{v:.2f}"
    for strategy, result in comparisons.items():
        r, c = result["out_of_sample"], result["config"]
        lines.append(f"| {strategy} | {c['exit_style']} / {c['atr_multiple']} | {r['return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {fmt(r['expectancy_r'])} | {fmt(r['win_rate_pct'])}% | {r['closed_trades']} | {result['recommendation']} |")
    for strategy, result in comparisons.items():
        lines += ["", f"## {strategy}", "", "| Metric | Training | Validation | Later test | Full period (descriptive) |", "|---|---:|---:|---:|---:|"]
        for key in ("return_pct", "annualized_pct", "expectancy_inr", "expectancy_r", "average_winner", "average_loser", "profit_factor", "max_drawdown_pct", "sharpe_zero_rf", "sortino_zero_rf", "closed_trades", "average_holding_sessions", "transaction_costs", "open_positions", "estimated_liquidation_equity"):
            lines.append(f"| {key} | " + " | ".join(fmt(result[period][key]) for period in ("training", "validation", "out_of_sample", "full_period")) + " |")
        lines += ["", "Year-wise and regime results (full period; includes selection data):", "", "| Group | Sessions | Compounded daily returns | Closed trades | Trade P&L |", "|---|---:|---:|---:|---:|"]
        for group in (result["full_period"]["yearwise"], result["full_period"]["regimes"]):
            for key, r in group.items():
                lines.append(f"| {key} | {r['sessions']} | {r['compounded_session_return_pct']:.2f}% | {r['closed_trades']} | {r['net_trade_pnl']:.2f} |")
        lines += ["", "Walk-forward folds:"]
        for f in result["walk_forward"]:
            r, c = f["result"], f["config"]
            lines.append(f"- {r['start']} to {r['end']}: {c['exit_style']}/{c['atr_multiple']}, return {r['return_pct']:.2f}%, expectancy R {fmt(r['expectancy_r'])}, {r['closed_trades']} closed trades.")
        lines.append(f"- Doubled slippage/brokerage/depository stress: {result['cost_stress']['return_pct']:.2f}% later return.")
    lines += ["", "## Parameter sensitivity (training only)", "", "| Strategy | Exit | ATR | Return | Drawdown | Trades |", "|---|---|---:|---:|---:|---:|"]
    for t in trials:
        c, r = t["config"], t["training"]
        lines.append(f"| {c['strategy']} | {c['exit_style']} | {c['atr_multiple']} | {r['return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['closed_trades']} |")
    lines += ["", "## Limitations", ""] + ["- "+note for note in protocol["limitations"]]
    lines += ["- Strategy selection is sensitive to sparse samples and parameters. No model accuracy or future return is guaranteed.",
              "- Regime return attribution compounds only days in that regime; trade P&L is grouped by entry regime. These answer different questions.",
              "", "[Full results, trades, itemized fees, skips, stop changes and equity history](./results.json)",
              "", "Cost sources: [NSE levies](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies), [NSE transaction/IPFT schedule](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf)."]
    (ROOT / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
