"""Replay saved INDstocks history; never infer option profits from index candles."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pandas as pd

from backend.indstocks_monitor import completed_bars
from screener.order_block import OrderBlockConfig, scan


def outcomes(bars: pd.DataFrame, setups: list[dict], force_close: str = "15:15",
             entry_minutes: int = 1) -> list[dict]:
    """Fixed-stop/target underlying diagnostic; next-bar validity, stop-first ambiguity.

    No partial exits, trailing, option conversion, fees or slippage. Gaps through a
    stop fill at the adverse open. Missing minutes leave outcomes unresolved.
    """
    if entry_minutes < 1:
        raise ValueError("Entry validity must be positive")
    trades = []
    busy_until = None
    for setup in sorted((s for s in setups if s["accepted"]), key=lambda s: s["timestamp"]):
        at = pd.Timestamp(setup["timestamp"])
        trade = {**setup, "points": None, "r_multiple": None, "scope": "underlying_gross_diagnostic"}
        if busy_until is not None and at <= busy_until:
            trades.append({**trade, "result": "POSITION_OPEN"})
            continue
        if at not in bars.index:
            trades.append({**trade, "result": "MISSING_ENTRY_BAR"})
            continue
        sign = 1 if setup["side"] == "long" else -1
        trigger, stop, target = setup["entry"], setup["stop"], setup["target"]
        fill_at = None
        pending_result = "EXPIRED_UNFILLED"
        for offset in range(entry_minutes):
            candidate = at+pd.Timedelta(minutes=offset)
            if candidate.date() != at.date() or candidate.strftime("%H:%M") >= "15:00":
                pending_result = "ENTRY_SESSION_ENDED"
                break
            if candidate not in bars.index:
                pending_result = "MISSING_ENTRY_BAR"
                break
            first = bars.loc[candidate]
            hit = first.High >= trigger if sign == 1 else first.Low <= trigger
            # Adverse opening invalidation is known before any intrabar trigger.
            if sign*(first.Open-stop) <= 0 and entry_minutes > 1:
                pending_result = "INVALIDATED_BEFORE_ENTRY"
                break
            if hit:
                fill_at = candidate
                break
            if first.Low <= stop if sign == 1 else first.High >= stop:
                pending_result = "INVALIDATED_BEFORE_ENTRY"
                break
        if fill_at is None:
            trades.append({**trade, "result": pending_result})
            continue
        at = fill_at
        first = bars.loc[at]
        gap_fill = bool(sign*(first.Open-trigger) >= 0)
        fill = float(first.Open if gap_fill else trigger)
        risk = sign*(fill-stop)
        if risk <= 0 or target is None or sign*(target-fill) <= 0:
            trades.append({**trade, "result": "INVALID_GAP_FILL"})
            continue
        trade.update(fill=fill, fill_risk=risk, gap_fill=gap_fill, fill_at=at.isoformat())
        previous = at-pd.Timedelta(minutes=1)
        result, exit_price, exit_at = "INCOMPLETE_DATA", None, None
        same_day = bars[(bars.index >= at) & (bars.index.date == at.date())]
        for stamp, bar in same_day.iterrows():
            if stamp-previous != pd.Timedelta(minutes=1):
                break
            previous = stamp
            if stamp.strftime("%H:%M") >= force_close:
                result, exit_price, exit_at = "TIME_EXIT", float(bar.Open), stamp
                break
            stop_hit = bar.Low <= stop if sign == 1 else bar.High >= stop
            target_hit = bar.High >= target if sign == 1 else bar.Low <= target
            if stop_hit:
                exit_price = min(stop, bar.Open) if sign == 1 else max(stop, bar.Open)
                if stamp == at and not gap_fill:
                    exit_price = stop  # Open preceded the intrabar fill.
                result, exit_at = "STOP", stamp
                trade["ohlc_order_uncertain"] = bool(target_hit or (stamp == at and not gap_fill))
                break
            if target_hit:
                result, exit_price, exit_at = "TARGET", target, stamp
                break
        if exit_at is not None:
            points = sign*(exit_price-fill)
            trade.update(points=round(points, 4), r_multiple=round(points/risk, 4),
                         exit_price=float(exit_price), exit_bar=exit_at.isoformat())
            busy_until = exit_at
        else:
            busy_until = at.normalize()+pd.Timedelta(days=1)-pd.Timedelta(minutes=1)
        trades.append({**trade, "result": result})
    return trades


def replay(source: Path, output: Path) -> dict:
    """Write a reproducible price/volume diagnostic with explicit coverage limits."""
    raw = json.loads(source.read_text())
    reports = {}
    for symbol, payload in raw["data"].items():
        candles = payload.get("candles", [])
        if not candles:
            continue
        end = pd.Timestamp(max(x["ts"] for x in candles), unit="s", tz="UTC") + pd.Timedelta(minutes=1)
        bars, _ = completed_bars(candles, end)
        reports[symbol] = scan(bars, end)
        reports[symbol]["outcomes"] = outcomes(bars, reports[symbol]["setups"])
    summary = {symbol: {"bars": len(r["rankings"]),
                       "zones": sum(z["event"] == "activated" for z in r["zones"]),
                       "accepted_setups": sum(s["accepted"] for s in r["setups"]),
                       "rejected_setups": sum(not s["accepted"] for s in r["setups"]),
                       "rejection_reasons": dict(Counter(x for s in r["setups"] for x in s["reason_codes"])),
                       "oi_available": False, "benchmark_available": False,
                       "diagnostic_completed": sum(t["points"] is not None for t in r["outcomes"]),
                       "diagnostic_gross_points": round(sum(t["points"] for t in r["outcomes"] if t["points"] is not None), 4),
                       "diagnostic_gross_r": round(sum(t["r_multiple"] for t in r["outcomes"] if t["r_multiple"] is not None), 4)}
               for symbol, r in reports.items()}
    result = {"source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "config": asdict(OrderBlockConfig()), "summary": summary, "reports": reports,
              "orders_enabled": False, "fills_enabled": False,
              "limitations": ["Historical replay, not live rankings", "No historical OI or benchmark supplied",
                              "No option execution simulation or profitability claim"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.source, args.output), indent=2))
