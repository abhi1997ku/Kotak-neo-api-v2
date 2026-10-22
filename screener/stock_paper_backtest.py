"""Reproducible, isolated 1y/2y replay of the unchanged stock-paper rules.

Uses the production PaperStore.scan for each historical after-close decision.
Downloads are cached; paper account DB is never opened by this runner.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, date
import gzip
import hashlib
import json
from pathlib import Path
import tempfile

from backend.stock_paper_service import download
from backend.watchlist_catalog import get_index_constituents, constituents_source
from screener.stock_paper import IST, INITIAL_CAPITAL, FEE, SLIPPAGE, PaperStore

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "screener" / "index_research_cache" / "stock_paper_backtest"


class HistoricalView:
    """Restore each decision date's share units, without future signal inputs.

    Yahoo OHLC is split-adjusted even with auto_adjust=False. Multiply the
    entire history visible on a decision day by splits *after* that day; volume
    uses the inverse factor. Past splits remain adjusted in indicator windows.
    The execution engine then applies splits to held shares on their ex-dates.
    This avoids double-adjusting held positions during a historical replay.
    """
    def __init__(self, frames):
        self.frames = frames
        self.dates = {s: [b["date"] for b in bars] for s, bars in frames.items()}
        self.factors = {}
        for symbol, bars in frames.items():
            factors = [1.0] * (len(bars) + 1)
            for i in range(len(bars) - 1, -1, -1):
                factors[i] = factors[i + 1] * (bars[i].get("split", 0) or 1)
            self.factors[symbol] = factors

    def at(self, day):
        result = {}
        for symbol, bars in self.frames.items():
            count = bisect_right(self.dates[symbol], day)
            prefix = bars[:count]
            factor = self.factors[symbol][count]
            if factor != 1:
                prefix = [dict(b, **{k: b[k] * factor for k in ("open", "high", "low", "close")},
                               volume=b["volume"] / factor, dividend=b.get("dividend", 0) * factor)
                          for b in prefix]
            result[symbol] = prefix
        return result


def statistics(account, start, end, benchmark):
    points = account["equity_history"]
    closed = [t for t in account["trades"] if t["status"] != "OPEN"]
    opened = [t for t in account["trades"] if t["status"] == "OPEN"]
    wins = [t["net_pnl"] for t in closed if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in closed if t["net_pnl"] < 0]
    last_equity = points[-1]["equity"]
    peak = INITIAL_CAPITAL
    drawdown = 0.0
    for p in points:
        peak = max(peak, p["equity"])
        drawdown = max(drawdown, (peak - p["equity"]) / peak * 100)
    grouped = defaultdict(list)
    for p in points:
        grouped[p["date"][:7]].append(p)
    monthly = []
    baseline = INITIAL_CAPITAL
    for month, group in sorted(grouped.items()):
        value = group[-1]["equity"]
        # Boundary months are conservatively marked partial, even if a calendar
        # boundary happened to coincide with the first/last trading session.
        partial = month in {start[:7], end[:7]}
        monthly.append({"month": month, "start_equity": baseline, "end_equity": value,
                        "net_change": value - baseline, "return_pct": (value / baseline - 1) * 100,
                        "partial": partial,
                        "entries": sum(t["entry_date"][:7] == month for t in account["trades"])})
        baseline = value
    complete = [m for m in monthly if not m["partial"]]
    elapsed = (date.fromisoformat(end) - date.fromisoformat(start)).days
    liquidation_cost = sum(t["quantity"] * t["mark"] * (1 - (1 - SLIPPAGE) * (1 - FEE)) for t in opened)
    return {"start": start, "end": end, "sessions": len(points),
            "initial_capital": INITIAL_CAPITAL, "ending_equity": last_equity,
            "estimated_liquidation_equity": last_equity - liquidation_cost,
            "net_change": last_equity - INITIAL_CAPITAL,
            "return_pct": (last_equity / INITIAL_CAPITAL - 1) * 100,
            "cagr_pct": ((last_equity / INITIAL_CAPITAL) ** (365.25 / elapsed) - 1) * 100 if elapsed else 0,
            "max_drawdown_pct": drawdown, "filled_entries": len(account["trades"]),
            "closed_trades": len(closed), "open_positions": len(opened),
            "win_rate_pct": len(wins) / len(closed) * 100 if closed else None,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            "average_closed_pnl": sum(t["net_pnl"] for t in closed) / len(closed) if closed else None,
            "realized_pnl": sum(t["net_pnl"] for t in closed),
            "exit_counts": dict(Counter(t["status"] for t in closed)),
            "skipped_counts": dict(Counter(o["outcome_reason"] for o in account["orders"] if o["status"] == "SKIPPED")),
            "complete_months": len(complete), "months_at_least_5pct": sum(m["return_pct"] >= 5 for m in complete),
            "average_complete_month_pct": sum(m["return_pct"] for m in complete) / len(complete) if complete else None,
            "nifty50_price_return_pct": (benchmark[-1]["close"] / benchmark[0]["close"] - 1) * 100,
            "monthly": monthly, "equity_curve": points, "trades": account["trades"],
            "orders": account["orders"]}


def replay(dataset, years, output):
    frames, members = dataset["frames"], dataset["members"]
    benchmark = frames["^NSEI"]
    end = date.fromisoformat(benchmark[-1]["date"])
    try:
        cutoff = end.replace(year=end.year - years).isoformat()
    except ValueError:
        cutoff = end.replace(year=end.year - years, day=28).isoformat()
    sessions = [b["date"] for b in benchmark if b["date"] >= cutoff]
    if not sessions or sum(b["date"] < sessions[0] for b in benchmark) < 200:
        raise ValueError("Insufficient benchmark warmup for the requested test period")
    view = HistoricalView(frames)
    # Temporary DB lives under report output, never under the live account path.
    # Production persistence/queue logic is exercised unchanged at every step.
    with tempfile.TemporaryDirectory(prefix=f"replay_{years}y_", dir=output) as directory:
        store = PaperStore(Path(directory) / "account.sqlite3")
        for i, day in enumerate(sessions):
            now = datetime.fromisoformat(day + "T17:00:00").replace(tzinfo=IST)
            snapshot = store.scan(view.at(day), members, now, dataset["universe_source"])
            if snapshot["warnings"]:
                raise ValueError(f"Incomplete {years}y replay on {day}: {snapshot['warnings']}")
            if i % 50 == 0:
                print(f"{years}y: {i+1}/{len(sessions)} sessions, {len(snapshot['trades'])} entries", flush=True)
        account = store.read()
    return statistics(account, sessions[0], sessions[-1], [b for b in benchmark if b["date"] >= sessions[0]])


def report_markdown(report):
    lines = ["# Stock swing historical backtest", "", f"Generated: {report['generated_at']}", "",
             "Unchanged daily breakout rules; independent ₹100,000 account for each window. No parameters were optimized on these results.", "",
             "| Metric | Last 1 year | Last 2 years |", "|---|---:|---:|"]
    specs = [("Period", lambda r: f"{r['start']} to {r['end']}"),
             ("Ending marked equity", lambda r: f"₹{r['ending_equity']:,.2f}"),
             ("Net change", lambda r: f"₹{r['net_change']:,.2f}"),
             ("Total return", lambda r: f"{r['return_pct']:.2f}%"),
             ("Annualized return", lambda r: f"{r['cagr_pct']:.2f}%"),
             ("Maximum daily-close drawdown", lambda r: f"{r['max_drawdown_pct']:.2f}%"),
             ("Filled / closed trades", lambda r: f"{r['filled_entries']} / {r['closed_trades']}"),
             ("Closed-trade win rate", lambda r: f"{r['win_rate_pct']:.2f}%" if r['win_rate_pct'] is not None else "N/A"),
             ("Profit factor", lambda r: f"{r['profit_factor']:.2f}" if r['profit_factor'] is not None else "N/A"),
             ("Full months reaching 5%", lambda r: f"{r['months_at_least_5pct']} / {r['complete_months']}"),
             ("Average full-month return", lambda r: f"{r['average_complete_month_pct']:.2f}%" if r['average_complete_month_pct'] is not None else "N/A"),
             ("Open positions at end", lambda r: str(r['open_positions'])),
             ("Estimated equity after closing open positions", lambda r: f"₹{r['estimated_liquidation_equity']:,.2f}"),
             ("Nifty 50 price-only return", lambda r: f"{r['nifty50_price_return_pct']:.2f}%")]
    for title, formatter in specs:
        lines.append(f"| {title} | {formatter(report['results']['1'])} | {formatter(report['results']['2'])} |")
    for years, result in report["results"].items():
        lines += ["", f"## Monthly results — {years} year window", "",
                  "| Month | Equity change | Return | Entries | Coverage |", "|---|---:|---:|---:|---|"]
        for m in result["monthly"]:
            lines.append(f"| {m['month']} | ₹{m['net_change']:,.2f} | {m['return_pct']:.2f}% | {m['entries']} | {'Partial boundary month' if m['partial'] else 'Full month'} |")
    lines += ["", "## Assumptions and limitations", ""] + [f"- {note}" for note in report["limitations"]]
    lines += ["", "Full trade, order and equity records: [results.json](./results.json).",
              "Provider split behavior: [yfinance maintainer explanation](https://github.com/ranaroussi/yfinance/issues/2660)."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Redownload real source history")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cache = OUTPUT / "source_history.json.gz"
    if cache.exists() and not args.refresh:
        with gzip.open(cache, "rt", encoding="utf-8") as stream:
            dataset = json.load(stream)
        print(f"Using saved real history fetched {dataset['fetched_at']}", flush=True)
    else:
        now = datetime.now(IST)
        members = get_index_constituents("nifty500")
        print(f"Downloading 1,200 calendar days for {len(members)} constituents plus Nifty 50", flush=True)
        frames = download(["^NSEI"] + list(dict.fromkeys(m['symbol'] for m in members)), now, history_days=1200)
        if not frames.get("^NSEI"):
            raise ValueError("Benchmark download unavailable")
        dataset = {"fetched_at": now.isoformat(), "members": members, "frames": frames,
                   "universe_source": constituents_source("nifty500")}
        with gzip.open(cache, "wt", encoding="utf-8") as stream:
            json.dump(dataset, stream, allow_nan=False)
    unavailable = [m["symbol"] for m in dataset["members"] if not dataset["frames"].get(m["symbol"])]
    report = {"generated_at": datetime.now(IST).isoformat(), "data_fetched_at": dataset["fetched_at"],
              "universe_source": dataset["universe_source"], "universe_count": len(dataset["members"]),
              "unavailable_symbols": unavailable,
              "strategy_sha256": hashlib.sha256((ROOT / "screener/stock_paper.py").read_bytes()).hexdigest(),
              "source_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
              "limitations": [
                  "Today's constituent list and sector labels are used historically: survivorship and membership bias. This is not a point-in-time-universe validation.",
                  f"Universe source: {dataset['universe_source']}. {len(unavailable)} symbols have no downloaded history; newer listings are ineligible until 200 bars exist.",
                  "Daily bars only; next-session opening entries, stop-first for ambiguous bars, adverse gap-stop fills. No intraday execution validation.",
                  "0.10% costs per side and 0.05% adverse slippage. Costs are model assumptions, not a broker fee/tax schedule.",
                  "Yahoo split-adjusted history is restored to each decision date's share units; known splits adjust held shares. Dividends use a simplified ex-date cash credit; other corporate reorganizations are not modeled.",
                  "Provider data can be revised or contain errors. No synthetic fills are used; any missing active-symbol session aborts the replay.",
                  "Signals see only bars on or before each simulated scan. 200 benchmark warmup bars are required. No historical signals are imported into the live paper account.",
                  "Return and drawdown use daily marked equity. Open holdings remain open at the end; estimated liquidation equity is also shown. Cash earns no interest.",
                  "Boundary months are excluded from the 5% monthly-goal count. Nifty comparison is price-only, before costs and dividends.",
                  "These are retrospective, unoptimized results, not proof of future performance or a guaranteed 5% monthly return."],
              "results": {}}
    for years in (1, 2):
        report["results"][str(years)] = replay(dataset, years, OUTPUT)
        (OUTPUT / f"{years}y.json").write_text(json.dumps(report["results"][str(years)], indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT / "results.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT / "report.md").write_text(report_markdown(report), encoding="utf-8")
    print(json.dumps({years: {k: r[k] for k in ("start", "end", "return_pct", "ending_equity", "max_drawdown_pct", "filled_entries", "win_rate_pct", "months_at_least_5pct", "complete_months")} for years, r in report["results"].items()}, indent=2), flush=True)


if __name__ == "__main__":
    main()
