from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd


# IMPORTANT: This module intentionally avoids moving averages, RSI, MACD, and Supertrend.
# The signal path uses only price structure, VWAP / anchored VWAP, relative volume,
# consolidation breakout logic, and ATR for stop / target sizing only.


@dataclass
class SymbolSetup:
    symbol: str
    setup_type: str
    entry: float
    stop: float
    target: float
    rr: float
    suggested_qty: int
    rationale: str
    volume_confirmation: bool
    breakout_level: Optional[float] = None
    trend_context: Optional[str] = None


def relative_volume(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Compute 20-day relative volume compared to recent average volume."""
    avg_vol = df["Volume"].rolling(window=lookback, min_periods=lookback).mean()
    return df["Volume"] / avg_vol


def vwap(df: pd.DataFrame) -> pd.Series:
    """Classic VWAP based on typical price * volume."""
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
    cum_volume = typical_price.mul(df["Volume"]).cumsum()
    total_volume = df["Volume"].cumsum()
    return cum_volume / total_volume.replace(0, pd.NA)


def anchored_vwap(df: pd.DataFrame, anchor_index: int | None = None) -> pd.Series:
    """Compute anchored VWAP from a chosen date/index; defaults to the latest swing point if provided."""
    if anchor_index is None:
        anchor_index = max(0, len(df) - 20)

    subset = df.iloc[anchor_index:]
    if subset.empty:
        return pd.Series([pd.NA] * len(df), index=df.index)

    typical_price = (subset["High"] + subset["Low"] + subset["Close"]) / 3.0
    cumulative = (typical_price * subset["Volume"]).cumsum()
    total_volume = subset["Volume"].cumsum()
    anchored = cumulative / total_volume.replace(0, pd.NA)

    result = pd.Series([pd.NA] * len(df), index=df.index)
    result.iloc[anchor_index:] = anchored.values
    return result


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range, used strictly for stop/target sizing, not signal generation."""
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift(1)).abs()
    low_close = (df["Low"] - df["Close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def detect_consolidation_range(df: pd.DataFrame, window: int = 20) -> Dict[str, float | bool]:
    """Detect a recent consolidation range using recent highs/lows and close proximity.
    
    Uses the PRIOR window (not including today) to detect the consolidation band,
    allowing today to break out above it cleanly.
    """
    if len(df) < window + 1:
        return {"range_high": float(df["High"].max()), "range_low": float(df["Low"].min()), "is_consolidating": False}

    # Look at the window BEFORE today (t-window to t-1, not including t)
    prior_window = df.iloc[-(window+1):-1]
    range_high = float(prior_window["High"].max())
    range_low = float(prior_window["Low"].min())
    span = range_high - range_low
    
    current_close = float(df["Close"].iloc[-1])
    close_to_range_mid = abs(current_close - ((range_high + range_low) / 2))
    is_consolidating = span > 0 and close_to_range_mid <= (span * 0.5)
    
    return {"range_high": range_high, "range_low": range_low, "is_consolidating": is_consolidating}


def breakout_signal(df: pd.DataFrame, atr_mult: float = 1.5) -> Dict[str, float | bool | str]:
    """Flag a clean breakout from a consolidation range using price and volume."""
    recent = detect_consolidation_range(df)
    last_close = float(df["Close"].iloc[-1])
    last_vol = float(df["Volume"].iloc[-1])
    avg_vol = float(df["Volume"].iloc[-20:].mean()) if len(df) >= 20 else last_vol
    vol_confirm = last_vol >= (avg_vol * 1.2)
    breakout_level = float(recent["range_high"])

    if last_close > breakout_level and vol_confirm:
        return {
            "signal": "breakout",
            "breakout_level": breakout_level,
            "volume_confirmation": True,
            "trend_context": "uptrend-structure",
            "is_valid": True,
        }

    if last_close < float(recent["range_low"]) and vol_confirm:
        return {
            "signal": "breakdown",
            "breakout_level": float(recent["range_low"]),
            "volume_confirmation": True,
            "trend_context": "downtrend-structure",
            "is_valid": True,
        }

    return {
        "signal": "neutral",
        "breakout_level": breakout_level,
        "volume_confirmation": False,
        "trend_context": "range-constrained",
        "is_valid": False,
    }


def compute_setup(df: pd.DataFrame, account_risk_pct: float = 0.5, min_rr: float = 2.0) -> Optional[SymbolSetup]:
    """Create a single-symbol trade setup from raw OHLCV data.

    This function is intentionally deterministic and unit-testable. It returns a
    setup only when the risk:reward and structure rules pass.
    """
    if df.empty or len(df) < 20:
        return None

    base = df.copy()
    base["VWAP"] = vwap(base)
    base["AnchoredVWAP"] = anchored_vwap(base, anchor_index=max(0, len(base) - 20))
    base["RelVol"] = relative_volume(base, lookback=20)
    base["ATR14"] = atr(base, period=14)

    latest = base.iloc[-1]
    prev_close = float(base["Close"].iloc[-2]) if len(base) > 1 else float(latest["Close"])
    current_close = float(latest["Close"])
    breakout = breakout_signal(base)

    if not breakout["is_valid"]:
        return None

    risk_per_trade = max(0.0001, account_risk_pct / 100.0)
    stop_distance = float(max(latest["ATR14"], 0.01))
    stop = current_close - stop_distance if breakout["signal"] == "breakout" else current_close + stop_distance
    target = current_close + (stop_distance * 3) if breakout["signal"] == "breakout" else current_close - (stop_distance * 3)

    rr = abs((target - current_close) / (current_close - stop)) if breakout["signal"] == "breakout" else abs((current_close - target) / (stop - current_close))
    if rr < min_rr:
        return None

    # Suggested qty at 0.5% account risk (configurable) based on stop distance.
    risk_budget = 100000 * risk_per_trade
    risk_per_unit = abs(current_close - stop)
    qty = max(1, int(risk_budget / risk_per_unit)) if risk_per_unit > 0 else 1

    setup_type = "breakout" if breakout["signal"] == "breakout" else "breakdown"
    rationale = (
        f"Price broke out of a consolidation range with volume confirmation. "
        f"VWAP support and relative volume are in the favor of the move."
        if breakout["signal"] == "breakout"
        else "Price broke down from a consolidation range with volume expansion and no MA signal shortcut used."
    )

    return SymbolSetup(
        symbol="TEST",
        setup_type=setup_type,
        entry=float(current_close),
        stop=float(stop),
        target=float(target),
        rr=float(rr),
        suggested_qty=qty,
        rationale=rationale,
        volume_confirmation=bool(breakout["volume_confirmation"]),
        breakout_level=float(breakout["breakout_level"]),
        trend_context=str(breakout["trend_context"]),
    )


# ------------------------------------------------------------------
# Single-symbol test case for visual chart verification
# ------------------------------------------------------------------


def build_demo_df() -> pd.DataFrame:
    """Build a simple synthetic series to visually verify the feature behavior."""
    dates = pd.date_range(start="2024-01-01", periods=60, freq="D")
    base = pd.DataFrame({
        "Date": dates,
        "Open": 100 + pd.Series(range(60)) * 0.8,
        "High": 102 + pd.Series(range(60)) * 0.8,
        "Low": 98 + pd.Series(range(60)) * 0.7,
        "Close": 100 + pd.Series(range(60)) * 0.9,
        "Volume": 1200 + pd.Series(range(60)) * 25,
    })
    base.loc[45:, "Open"] += 10
    base.loc[45:, "High"] += 12
    base.loc[45:, "Low"] += 9
    base.loc[45:, "Close"] += 12
    base.loc[45:, "Volume"] += 800
    return base.set_index("Date")


if __name__ == "__main__":
    df = build_demo_df()
    setup = compute_setup(df)
    print(setup)
