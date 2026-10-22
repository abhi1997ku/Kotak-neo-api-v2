"""Prespecified offline frequency comparison. Does not promote any configuration."""
from dataclasses import replace
from pathlib import Path
from collections import Counter
import json

import pandas as pd

from backend.indstocks_monitor import completed_bars
from screener.order_block import OrderBlockConfig, scan
from screener.order_block_research import outcomes


def compare() -> dict:
    variants = {"baseline": (OrderBlockConfig(), 1),
                "entry_3m": (OrderBlockConfig(), 3),
                "volume_ranking_only": (replace(OrderBlockConfig(), min_relative_volume=0), 1),
                "volume_ranking_entry_3m": (replace(OrderBlockConfig(), min_relative_volume=0), 3)}
    sources = {"Oct12_16": Path("backend/data/indstocks_validation_7days/raw.json"),
               "Oct05_09": Path("backend/data/indstocks_history/2026-10-05_09.json")}
    report = {"scope": "Gross underlying fixed-stop/target diagnostics, not option P&L", "weeks": {}}
    for week, source in sources.items():
        data = json.loads(source.read_text())["data"]
        candles = data["NFO_48699"]["candles"]
        end = pd.Timestamp(max(x["ts"] for x in candles), unit="s", tz="UTC")+pd.Timedelta(minutes=1)
        bars, _ = completed_bars(candles, end)
        rows = {}
        cached = {}
        for name, (config, validity) in variants.items():
            if config not in cached:
                cached[config] = scan(bars, end, config)
            signals = cached[config]["setups"]
            trades = outcomes(bars, signals, entry_minutes=validity)
            filled = [t for t in trades if "fill" in t]
            complete = [t for t in filled if t["points"] is not None]
            daily = {str(d): sum(pd.Timestamp(t["fill_at"]).date() == d for t in filled) for d in sorted(set(bars.index.date))}
            rows[name] = {"accepted": sum(s["accepted"] for s in signals), "filled": len(filled),
                "completed": len(complete), "wins": sum(t["points"] > 0 for t in complete),
                "gross_points": round(sum(t["points"] for t in complete), 4),
                "gross_r": round(sum(t["r_multiple"] for t in complete), 4),
                "daily_fills": daily, "results": dict(Counter(t["result"] for t in trades)), "trades": trades}
        report["weeks"][week] = rows
    output = Path("backend/data/order_block_comparison.json")
    output.write_text(json.dumps(report, indent=2, allow_nan=False))
    return {week: {name: {k: v for k, v in row.items() if k != "trades"} for name, row in rows.items()}
            for week, rows in report["weeks"].items()}


if __name__ == "__main__":
    print(json.dumps(compare(), indent=2))
