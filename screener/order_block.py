"""Causal, paper-only order-block research. No provider or execution dependencies."""
from dataclasses import dataclass
import math

import pandas as pd

from screener.feature_engine import atr, vwap


@dataclass(frozen=True)
class OrderBlockConfig:
    pivot_bars: int = 2
    atr_period: int = 14
    momentum_bars: int = 10
    volume_bars: int = 20
    origin_bars: int = 10
    zone_lifetime: int = 60
    opening_minutes: int = 15
    displacement_atr: float = 1.0
    min_efficiency: float = 0.3
    max_vwap_crossings: int = 4
    min_relative_volume: float = 1.0
    max_stop_atr: float = 2.0
    min_reward_risk: float = 1.5
    tick_size: float = 0.05
    entry_start: str = "09:20"
    entry_end: str = "15:00"
    require_oi: bool = False
    momentum_weight: float = 30.0
    strength_weight: float = 30.0
    volume_weight: float = 20.0
    oi_weight: float = 20.0
    momentum_scale: float = 2.0
    strength_scale: float = 0.005
    volume_scale: float = 2.0
    oi_scale: float = 0.02


def validate(bars: pd.DataFrame) -> None:
    """Reject ambiguous timestamps and invalid prices rather than repairing them."""
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("Timezone-aware candle-open timestamps required")
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError("Timestamps must be unique and increasing")
    values = bars[["Open", "High", "Low", "Close", "Volume"]]
    if not values.map(lambda x: isinstance(x, (float, int)) and math.isfinite(x)).all().all():
        raise ValueError("OHLCV must be finite numeric values")
    if ((values.Low <= 0) | (values.Volume < 0) |
        (values.High < values[["Open", "Close", "Low"]].max(axis=1)) |
        (values.Low > values[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError("Invalid OHLCV")
    if (bars.index != bars.index.floor("min")).any():
        raise ValueError("Minute-aligned candles required")


def scan(bars: pd.DataFrame, as_of: pd.Timestamp, config: OrderBlockConfig = OrderBlockConfig(),
         benchmark: pd.DataFrame | None = None, oi_kind: str | None = None) -> dict:
    """Return immutable zone events, closed-bar rankings and retest decisions.

    OI is used only for explicitly identified futures data with an OIAvailableAt
    timestamp on each sample. Benchmark data must be minute OHLCV. All timestamps
    label bar opens; observations become available one minute later.
    """
    c = config
    if min(c.pivot_bars, c.atr_period, c.momentum_bars, c.volume_bars, c.origin_bars,
           c.zone_lifetime, c.opening_minutes) < 1 or c.tick_size <= 0:
        raise ValueError("Periods and tick size must be positive")
    if any(x <= 0 for x in (c.momentum_scale, c.strength_scale, c.volume_scale, c.oi_scale)):
        raise ValueError("Score scales must be positive")
    if pd.Timestamp(as_of).tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    validate(bars)
    b = bars.copy()
    b.index = b.index.tz_convert("Asia/Kolkata")
    b = b[b.index + pd.Timedelta(minutes=1) <= as_of].between_time("09:15", "15:29")
    b["ATR"] = atr(b, c.atr_period).shift(1)
    bm = None
    if benchmark is not None:
        validate(benchmark)
        bm = benchmark.copy()
        bm.index = bm.index.tz_convert("Asia/Kolkata")
    result = {"zones": [], "rankings": [], "setups": [], "orders_enabled": False,
              "fills_enabled": False, "mode": "experimental-price-volume", "as_of": str(as_of)}
    previous = None
    for day, session in b.groupby(b.index.date):
        s = session.copy()
        s["VWAP"] = vwap(s)
        pivots = {"high": None, "low": None}
        active = []
        broken = set()
        for i, (stamp, bar) in enumerate(s.iterrows()):
            available = stamp + pd.Timedelta(minutes=1)
            window = s.iloc[max(0, i-c.momentum_bars):i+1]
            continuity = s.iloc[max(0, i-max(c.momentum_bars, c.volume_bars, c.atr_period)):i+1]
            gap = len(continuity) > 1 and (continuity.index.to_series().diff().dropna() != pd.Timedelta(minutes=1)).any()
            if i and stamp - s.index[i-1] != pd.Timedelta(minutes=1):
                active.clear()
                pivots = {"high": None, "low": None}
            n = c.pivot_bars
            if i >= 2*n:
                block = s.iloc[i-2*n:i+1]
                if (block.index.to_series().diff().dropna() == pd.Timedelta(minutes=1)).all():
                    pivot = block.iloc[n]
                    for key, col, compare in (("high", "High", max), ("low", "Low", min)):
                        others = list(block[col].iloc[:n]) + list(block[col].iloc[n+1:])
                        if (pivot[col] > compare(others) if key == "high" else pivot[col] < compare(others)):
                            pivots[key] = (i-n, float(pivot[col]))
            levels = [] if previous is None else [previous.High.max(), previous.Low.min()]
            levels += [p[1] for p in pivots.values() if p is not None]
            opening_end = stamp.normalize() + pd.Timedelta(hours=9, minutes=15+c.opening_minutes)
            opening = s[(s.index < opening_end) & (s.index >= stamp.normalize()+pd.Timedelta(hours=9, minutes=15))]
            if available >= opening_end and len(opening) == c.opening_minutes:
                levels += [opening.High.max(), opening.Low.min()]
            reasons = []
            if i < max(c.momentum_bars, c.volume_bars) or pd.isna(bar.ATR) or bar.ATR <= 0:
                reasons.append("WARMUP")
            if gap:
                reasons.append("DATA_GAP")
            change = float(window.Close.iloc[-1] - window.Close.iloc[0])
            travel = float(window.Close.diff().abs().sum())
            efficiency = abs(change)/travel if travel else 0.0
            signs = (window.Close > window.VWAP).astype(int)
            crossings = int(signs.diff().abs().sum())
            prior_vol = s.Volume.iloc[max(0, i-c.volume_bars):i].mean()
            rvol = float(bar.Volume/prior_vol) if prior_vol > 0 else 0.0
            if efficiency < c.min_efficiency or crossings > c.max_vwap_crossings:
                reasons.append("CHOPPY")
            if rvol < c.min_relative_volume:
                reasons.append("LOW_VOLUME")
            direction = 1 if change > 0 else -1
            strength = None
            if bm is not None and len(window) > c.momentum_bars:
                aligned = bm.reindex(window.index)
                if aligned.Close.notna().all():
                    strength = float(window.Close.iloc[-1]/window.Close.iloc[0] - aligned.Close.iloc[-1]/aligned.Close.iloc[0])
            oi_change = None
            if oi_kind == "futures" and {"OI", "OIAvailableAt"} <= set(window.columns) and len(window) > c.momentum_bars:
                oi_times = pd.to_datetime(window.OIAvailableAt, utc=True, errors="coerce")
                if (oi_times.notna().all() and (oi_times >= window.index).all()
                        and (oi_times <= window.index + pd.Timedelta(minutes=1)).all()
                        and window.OI.map(lambda x: pd.notna(x) and math.isfinite(x) and x > 0).all()):
                    oi_change = float(window.OI.iloc[-1]/window.OI.iloc[0]-1)
            if c.require_oi and oi_change is None:
                reasons.append("OI_UNAVAILABLE")
            clip = lambda x: max(0.0, min(1.0, float(x)))
            score = c.momentum_weight*clip(abs(change)/bar.ATR/c.momentum_scale) if bar.ATR > 0 else 0.0
            score += c.volume_weight*clip(rvol/c.volume_scale)
            if strength is not None:
                score += c.strength_weight*clip(direction*strength/c.strength_scale)
            if oi_change is not None:
                score += c.oi_weight*clip(oi_change/c.oi_scale)
            ranking = {"timestamp": available.isoformat(), "side": "long" if direction > 0 else "short",
                       "score": round(score, 4), "eligible": not reasons, "reason_codes": reasons,
                       "relative_volume": rvol, "efficiency": efficiency, "vwap_crossings": crossings,
                       "relative_strength": strength, "oi_change": oi_change,
                       "missing_features": (["OI"] if oi_change is None else []) + (["BENCHMARK"] if strength is None else []),
                       "support": sorted(set(float(x) for x in levels if x < bar.Close), reverse=True),
                       "resistance": sorted(set(float(x) for x in levels if x > bar.Close))}
            result["rankings"].append(ranking)
            # Process existing zones before creating new ones: no activation-bar retests.
            for z in active[:]:
                long = z["side"] == "long"
                invalid = bar.Low < z["low"] if long else bar.High > z["high"]
                if invalid or i-z["activation_index"] > c.zone_lifetime:
                    active.remove(z)
                    result["zones"].append({**z, "event": "invalidated" if invalid else "expired", "timestamp": available.isoformat()})
                    continue
                if not (bar.Low <= z["high"] and bar.High >= z["low"]):
                    continue
                why = list(reasons)
                if ranking["side"] != z["side"]:
                    why.append("MOMENTUM_CONFLICT")
                confirmed = bar.Close > bar.Open and bar.Close > z["high"] if long else bar.Close < bar.Open and bar.Close < z["low"]
                if not confirmed:
                    why.append("NO_REJECTION_CONFIRMATION")
                if not c.entry_start <= available.strftime("%H:%M") < c.entry_end:
                    why.append("OUTSIDE_SESSION")
                entry = float(bar.High+c.tick_size if long else bar.Low-c.tick_size)
                stop = z["low"]-c.tick_size if long else z["high"]+c.tick_size
                risk = abs(entry-stop)
                if risk > c.max_stop_atr*bar.ATR:
                    why.append("STOP_TOO_WIDE")
                targets = sorted([float(x) for x in levels if (x > entry if long else x < entry)], reverse=not long)
                target = targets[0] if targets else None
                if target is None or abs(target-entry) < c.min_reward_risk*risk:
                    why.append("INSUFFICIENT_TARGET_ROOM")
                result["setups"].append({"timestamp": available.isoformat(), "side": z["side"], "zone_id": z["zone_id"],
                    "entry": entry, "stop": stop, "risk": risk, "target": target, "accepted": not why,
                    "reason_codes": why, "valid_until": (available+pd.Timedelta(minutes=1)).isoformat(),
                    "execution_status": "NOT_SIMULATED", "itm_strikes": 2})
                if not why:
                    active.remove(z)
            if reasons and ("WARMUP" in reasons or "DATA_GAP" in reasons):
                continue
            for side, key in (("long", "high"), ("short", "low")):
                p = pivots[key]
                long = side == "long"
                if p is None or (key, p[0]) in broken or not i:
                    continue
                crossed = bar.Close > p[1] >= s.Close.iloc[i-1] if long else bar.Close < p[1] <= s.Close.iloc[i-1]
                if not crossed:
                    continue
                broken.add((key, p[0]))
                displacement = bar.Close-bar.Open if long else bar.Open-bar.Close
                if displacement < c.displacement_atr*bar.ATR:
                    continue
                origins = s.iloc[max(0, i-c.origin_bars):i]
                opposing = origins[origins.Close < origins.Open] if long else origins[origins.Close > origins.Open]
                if opposing.empty:
                    continue
                origin = opposing.iloc[-1]
                z = {"zone_id": f"{available.isoformat()}:{side}", "side": side,
                     "low": float(origin.Low), "high": float(origin.High), "origin": opposing.index[-1].isoformat(),
                     "activation_index": i, "activated_at": available.isoformat()}
                active.append(z)
                result["zones"].append({**z, "event": "activated", "timestamp": available.isoformat()})
        previous = s
    return result


def rank_universe(reports: dict[str, dict], timestamp: str) -> list[dict]:
    """Rank only simultaneous eligible observations with identical feature coverage."""
    rows = [{**r, "symbol": symbol} for symbol, report in reports.items() for r in report["rankings"]
            if r["timestamp"] == timestamp and r["eligible"]]
    coverage = {tuple(r["missing_features"]) for r in rows}
    if len(coverage) > 1:
        raise ValueError("Cannot compare rankings with different feature coverage")
    return sorted(rows, key=lambda r: (-r["score"], r["symbol"]))
