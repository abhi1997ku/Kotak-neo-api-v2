"""Offline five-minute EMA9/VWAP pullback experiment; no order placement."""
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import Counter
import json

import pandas as pd

from backend.indstocks_monitor import completed_bars
from screener.feature_engine import vwap
from screener.order_block import validate
from screener.order_block_research import outcomes


@dataclass(frozen=True)
class EMAConfig:
    ema_period: int = 9
    target_r: float = 1.5
    tick: float = 0.05
    start: str = "09:30"
    end: str = "15:00"
    validity_minutes: int = 5


def signals(one: pd.DataFrame, as_of: pd.Timestamp, p: EMAConfig = EMAConfig(),
            diagnostics: list[dict] | None = None) -> list[dict]:
    """Use completed five-minute bars, continuous EMA and session-reset minute VWAP.

    A prior close must already be on the trend side of its EMA. The signal bar
    touches its EMA and closes back above/below EMA and VWAP with matching body
    direction and EMA slope. First input session is warmup-only.
    """
    validate(one)
    b = one.copy()
    b.index = b.index.tz_convert("Asia/Kolkata")
    b = b[b.index+pd.Timedelta(minutes=1) <= as_of].between_time("09:15", "15:29")
    if b.empty:
        return []
    b["VWAP"] = b.groupby(b.index.date, group_keys=False).apply(lambda g: vwap(g))
    five = b.resample("5min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum", "VWAP": "last"})
    five = five[b.Close.resample("5min").count() == 5]
    five["EMA"] = five.Close.ewm(span=p.ema_period, adjust=False, min_periods=p.ema_period).mean()
    result = []
    for i in range(1, len(five)):
        bar, prior = five.iloc[i], five.iloc[i-1]
        stamp = five.index[i]
        at = stamp+pd.Timedelta(minutes=5)
        def reject(side: str, reasons: list[str]) -> None:
            if diagnostics is not None:
                diagnostics.append({"timestamp": at.isoformat(), "side": side,
                                    "accepted": False, "reason_codes": reasons})
        if stamp.date() == b.index[0].date() or pd.isna(prior.EMA):
            reject("both", ["WARMUP_HISTORY"])
            continue
        if stamp-five.index[i-1] != pd.Timedelta(minutes=5):
            reject("both", ["NONCONSECUTIVE_CONTEXT"])
            continue
        if not p.start <= at.strftime("%H:%M") < p.end:
            reject("both", ["OUTSIDE_ENTRY_SESSION"])
            continue
        long = prior.Close > prior.EMA and bar.Low <= bar.EMA < bar.Close and bar.Close > bar.VWAP and bar.Close > bar.Open and bar.EMA > prior.EMA
        short = prior.Close < prior.EMA and bar.High >= bar.EMA > bar.Close and bar.Close < bar.VWAP and bar.Close < bar.Open and bar.EMA < prior.EMA
        if not long and not short:
            for direction, checks in (
                ("long", [(prior.Close > prior.EMA, "PRIOR_CLOSE_NOT_ABOVE_EMA"),
                          (bar.Low <= bar.EMA, "NO_EMA_TOUCH"), (bar.Close > bar.EMA, "CLOSE_NOT_ABOVE_EMA"),
                          (bar.Close > bar.VWAP, "CLOSE_NOT_ABOVE_VWAP"), (bar.Close > bar.Open, "NOT_BULLISH_BAR"),
                          (bar.EMA > prior.EMA, "EMA_NOT_RISING")]),
                ("short", [(prior.Close < prior.EMA, "PRIOR_CLOSE_NOT_BELOW_EMA"),
                           (bar.High >= bar.EMA, "NO_EMA_TOUCH"), (bar.Close < bar.EMA, "CLOSE_NOT_BELOW_EMA"),
                           (bar.Close < bar.VWAP, "CLOSE_NOT_BELOW_VWAP"), (bar.Close < bar.Open, "NOT_BEARISH_BAR"),
                           (bar.EMA < prior.EMA, "EMA_NOT_FALLING")])):
                reject(direction, [reason for passed, reason in checks if not passed])
            continue
        side = 1 if long else -1
        entry = float(bar.High+p.tick if long else bar.Low-p.tick)
        stop = float(bar.Low-p.tick if long else bar.High+p.tick)
        risk = abs(entry-stop)
        result.append({"timestamp": at.isoformat(), "side": "long" if long else "short", "accepted": True,
                       "entry": entry, "stop": stop, "target": entry+side*p.target_r*risk,
                       "risk": risk, "valid_until": (at+pd.Timedelta(minutes=p.validity_minutes)).isoformat()})
    return result


def run() -> dict:
    sources = [Path("backend/data/indstocks_history/2026-10-05_09.json"), Path("backend/data/indstocks_validation_7days/raw.json")]
    candles = []
    for source in sources:
        candles += json.loads(source.read_text())["data"]["NFO_48699"]["candles"]
    end = pd.Timestamp(max(x["ts"] for x in candles), unit="s", tz="UTC")+pd.Timedelta(minutes=1)
    one, _ = completed_bars(candles, end)
    report = {"scope": "BANKNIFTY October futures; gross underlying diagnostic, not option P&L",
              "warmup_day": str(one.index[0].date()), "variants": {}}
    for target in (1.5, 2.0):
        p = EMAConfig(target_r=target)
        setups = signals(one, end, p)
        trades = outcomes(one, setups, entry_minutes=p.validity_minutes)
        complete = [t for t in trades if t["points"] is not None]
        equity = peak = drawdown = 0.0
        for t in complete:
            equity += t["r_multiple"]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
        daily = {str(d): sum(pd.Timestamp(t["fill_at"]).date() == d for t in trades if "fill_at" in t)
                 for d in sorted(set(one.index.date)) if d != one.index[0].date()}
        report["variants"][str(target)] = {"config": asdict(p), "setups": len(setups),
            "filled": sum("fill" in t for t in trades), "completed": len(complete),
            "win_rate": round(100*sum(t["points"] > 0 for t in complete)/len(complete), 2) if complete else None,
            "gross_r": round(equity, 4), "max_drawdown_r": round(drawdown, 4),
            "gross_points": round(sum(t["points"] for t in complete), 4), "daily_fills": daily,
            "results": dict(Counter(t["result"] for t in trades)), "trades": trades}
    Path("backend/data/ema_vwap_research.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    return {k: {key: val for key, val in v.items() if key != "trades"} for k, v in report["variants"].items()}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
