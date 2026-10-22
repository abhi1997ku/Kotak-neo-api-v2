"""Read-only candle replay; underlying forward moves are NOT option trade P&L.

Run: python -m screener.paper_signal_backtest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import math
import pandas as pd

from screener.feature_engine import adx, atr, vwap

from backend.paper_trading import BankNiftySignalEngine, OneMinuteCandle
from backend.paper_trend import OpeningTrendEngine

INDIA = ZoneInfo("Asia/Kolkata")

class EarlierEngine(BankNiftySignalEngine):
    """Freeze the pre-history implementation, including its rolling VWAP."""
    def __init__(self, initial_entry=False):
        super().__init__()
        self.initial_entry = initial_entry

    def _market_regime(self):
        if len(self.candles) < 25 or sum(c.volume for c in self.candles) <= 0:
            return "neutral"
        pv = vol = 0
        vwaps = []
        for c in self.candles:
            pv += (c.high+c.low+c.close)/3*c.volume
            vol += c.volume
            vwaps.append(pv/vol if vol else c.close)
        closes = [c.close for c in self.candles]
        moving = self._ema(vwaps[-21:], 21)
        fast, slow = self._ema(closes[-7:], 7), self._ema(closes[-25:], 25)
        if fast > moving and slow > moving and vwaps[-1] > moving:
            return "bullish"
        if fast < moving and slow < moving and vwaps[-1] < moving:
            return "bearish"
        return "neutral"

    def _short_trigger(self):
        first = not self._initialized
        signal = super()._short_trigger()
        return None if first and not self.initial_entry else signal


def replay(candles, variant, prior=()):
    if variant == "original":
        engine = EarlierEngine()
    elif variant == "initial_entry_fix":
        engine = EarlierEngine(initial_entry=True)
    elif variant == "updated":
        engine = BankNiftySignalEngine()
    elif variant == "opening_v3":
        engine = OpeningTrendEngine()
    else:
        raise ValueError("Unknown strategy variant")
    if not candles:
        return []
    engine.session_day = engine._candle_day(candles[0])
    if prior and variant in ("updated", "opening_v3"):
        if any(c.minute >= candles[0].minute or engine._candle_day(c) >= engine.session_day for c in prior):
            raise ValueError("Seed must precede the replay session")
        engine.load_completed_candles(list(prior)[-80:], allow_initial_signal=True)
    by_minute = {c.minute: c for c in candles}
    signals = []
    for candle in candles:
        engine.candles.append(candle)
        engine.candles = engine.candles[-(500 if variant in ("updated", "opening_v3") else 80):]
        direction = engine._short_trigger()
        if not direction:
            continue
        # Signal is known only after this candle closes. Never fill at its open/close.
        entry_minute = candle.minute+1
        entry = by_minute.get(entry_minute)
        at = datetime.fromtimestamp(entry_minute*60, INDIA)
        item = {"available_at_ist": at.isoformat(), "direction": direction,
                "entry_eligible": ("09:18" <= at.strftime("%H:%M") < "15:00") if variant == "opening_v3" else at.strftime("%H:%M") < "15:15",
                "next_open": entry.open if entry else None}
        sign = 1 if direction == "long" else -1
        for horizon in (5, 15, 30):
            # Require continuous observations and an exit at the exact horizon.
            exit_bar = by_minute.get(entry_minute+horizon)
            complete = all(m in by_minute for m in range(entry_minute, entry_minute+horizon+1))
            item[f"forward_{horizon}m_points"] = round(sign*(exit_bar.open-entry.open), 2) if entry and exit_bar and complete else None
        signals.append(item)
    return signals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("backend/paper_trading.db"))
    parser.add_argument("--output", type=Path, default=Path("screener/index_research_cache/paper_signal_backtest"))
    args = parser.parse_args()
    # Read-only snapshot; never instantiate the journal or mutate live-agent data.
    with sqlite3.connect(args.database.resolve().as_uri()+"?mode=ro", uri=True) as db:
        rows = db.execute("SELECT trade_date,minute,open,high,low,close,volume FROM paper_day_candles ORDER BY trade_date,minute").fetchall()
    days = defaultdict(list)
    excluded = 0
    for day, *values in rows:
        local_time = datetime.fromtimestamp(values[0]*60, INDIA).strftime("%H:%M")
        if not "09:15" <= local_time < "15:30":
            excluded += 1
            continue
        days[day].append(OneMinuteCandle(*values))
    report = {"scope": "signal replay, not an option execution backtest", "snapshot_sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
              "excluded_out_of_session_candles": excluded,
              "limitations": ["Only locally recorded futures audit candles; legacy rows do not store contract identity.",
                              "No prior-session contract candles: the earlier seeded opening rule is untested; opening_v3 uses today's candles only.",
                              "Signal replay does not simulate position occupancy, daily loss caps, cooldowns, option liquidity or entry rejection.",
                              "No historical option prices: no trade win rate, stops, targets, trailing exits or rupee P&L can be computed.",
                              "Forward underlying moves ignore fees/spread and may overlap; they are not executed trades.",
                              "This is the same session used to diagnose the issue, not out-of-sample validation."], "days": {}}
    for day, candles in days.items():
        result = {"candles": len(candles), "first_ist": datetime.fromtimestamp(candles[0].minute*60, INDIA).isoformat(),
                  "last_closed_bar_ist": datetime.fromtimestamp(candles[-1].minute*60, INDIA).isoformat(),
                  "missing_minutes": candles[-1].minute-candles[0].minute+1-len(candles), "prior_seed_candles": 0,
                  "variants": {}}
        for variant in ("original", "initial_entry_fix", "updated", "opening_v3"):
            signals = replay(candles, variant)
            result["variants"][variant] = {"signal_count": len(signals), "signals": signals}
        report["days"][day] = result
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (args.output/"candles.json").write_text(json.dumps(rows), encoding="utf-8")
    lines = ["# BANKNIFTY paper-signal replay", "", "This is a signal comparison, not an option P&L backtest.", "", f"Excluded {excluded} candles outside 09:15–15:30 IST.", ""]
    for day, result in report["days"].items():
        lines += [f"## {day}", "", f"{result['candles']} recorded candles; {result['missing_minutes']} missing minutes within the observed range. No prior-session seed.", "", "| Version | Signals | First signal (IST) | Direction | 5m / 15m / 30m forward futures points |", "|---|---:|---|---|---|"]
        for name, variant in result["variants"].items():
            first = variant["signals"][0] if variant["signals"] else {}
            moves = " / ".join(str(first.get(f"forward_{h}m_points", "—")) for h in (5, 15, 30))
            lines.append(f"| {name} | {variant['signal_count']} | {first.get('available_at_ist', '—')} | {first.get('direction', '—')} | {moves} |")
        lines.append("")
    lines += ["## Limits", ""] + [f"- {item}" for item in report["limitations"]]
    (args.output/"REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
