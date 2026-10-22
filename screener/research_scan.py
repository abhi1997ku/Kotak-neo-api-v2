"""Real-data, multi-horizon Indian equity research scan.

The scanner separates observed data from unavailable or partially checked
research areas. Missing evidence is shown as unavailable, never inferred.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from screener.feature_engine import adx

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - optional market data package
    yf = None

try:
    from backend.watchlist_catalog import constituents_source
    from screener.ai_pattern_model import HORIZONS, MIN_ANALOGS, MIN_DISTINCT_SYMBOLS, MODEL_NAME, score_universe_patterns
    from screener.data_fetcher import fetch_data_batch
    from screener.feed_imports import latest_delivery, latest_fii_dii, recent_corporate_events
    from screener.feature_engine import compute_setup
    from screener.universe_loader import load_universe_metadata
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    from data_fetcher import fetch_data_batch
    from feed_imports import latest_delivery, latest_fii_dii, recent_corporate_events
    from feature_engine import compute_setup
    from universe_loader import load_universe_metadata
    from ai_pattern_model import HORIZONS, MIN_ANALOGS, MIN_DISTINCT_SYMBOLS, MODEL_NAME, score_universe_patterns

    try:
        from backend.watchlist_catalog import constituents_source
    except ModuleNotFoundError:
        constituents_source = lambda: "Bundled Nifty 50 constituent snapshot"

logger = logging.getLogger(__name__)

INDIA_TZ = timezone(timedelta(hours=5, minutes=30), name="IST")
HISTORY_DAYS = 560

BENCHMARKS = {
    "NIFTY 50": "^NSEI",
    "NIFTY 500": "^CRSLDX",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY MIDCAP 50": "^NSEMDCP50",
    "NIFTY SMALLCAP": "^CNXSC",
    "INDIA VIX": "^INDIAVIX",
}
GLOBAL_MARKETS = {
    "S&P 500": "^GSPC",
    "NASDAQ": "^IXIC",
    "HANG SENG": "^HSI",
    "CRUDE OIL": "CL=F",
    "USD/INR": "INR=X",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 2) -> float | None:
    result = _number(value)
    return round(result, digits) if result is not None else None


def _return_pct(series: pd.Series, periods: int) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) <= periods or not float(clean.iloc[-periods - 1]):
        return None
    return float((clean.iloc[-1] / clean.iloc[-periods - 1] - 1) * 100)


def _technical_snapshot(frame: pd.DataFrame) -> dict[str, Any] | None:
    if frame is None or frame.empty:
        return None

    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    high = pd.to_numeric(frame["High"], errors="coerce").reindex(close.index)
    low = pd.to_numeric(frame["Low"], errors="coerce").reindex(close.index)
    volume = pd.to_numeric(frame["Volume"], errors="coerce").reindex(close.index).fillna(0)
    if len(close) < 20:
        return None

    high_low = high - low
    high_prev = (high - close.shift(1)).abs()
    low_prev = (low - close.shift(1)).abs()
    true_range = pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)
    atr14_series = true_range.rolling(14, min_periods=14).mean()

    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    losses = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    relative_strength = gains / losses.replace(0, pd.NA)
    rsi_series = 100 - (100 / (1 + relative_strength))
    rsi_series = rsi_series.mask((losses == 0) & (gains > 0), 100)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()

    adx_series = adx(pd.DataFrame({"High": high, "Low": low, "Close": close}), 14)

    weekly = frame.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])
    weekly_close = pd.to_numeric(weekly["Close"], errors="coerce").dropna()

    prior_volume = volume.iloc[-21:-1]
    average_volume = float(prior_volume.mean()) if not prior_volume.empty else 0.0
    latest_volume = float(volume.iloc[-1])
    vwap_volume = volume.replace(0, pd.NA)
    typical_price = (high + low + close) / 3
    full_vwap = (typical_price * volume).cumsum() / volume.cumsum().replace(0, pd.NA)
    recent_typical = typical_price.iloc[-20:]
    recent_volume = vwap_volume.iloc[-20:]
    anchored_vwap = (recent_typical * recent_volume).sum() / recent_volume.sum() if recent_volume.sum() else None

    last_date = close.index[-1]
    if hasattr(last_date, "date"):
        last_date = last_date.date().isoformat()

    return {
        "close": float(close.iloc[-1]),
        "previous_close": float(close.iloc[-2]),
        "daily_change_pct": _return_pct(close, 1),
        "return_5d_pct": _return_pct(close, 5),
        "return_20d_pct": _return_pct(close, 20),
        "return_60d_pct": _return_pct(close, 60),
        "return_120d_pct": _return_pct(close, 120),
        "sma20": float(close.rolling(20, min_periods=20).mean().iloc[-1]),
        "sma50": _number(close.rolling(50, min_periods=50).mean().iloc[-1]) if len(close) >= 50 else None,
        "sma100": _number(close.rolling(100, min_periods=100).mean().iloc[-1]) if len(close) >= 100 else None,
        "sma200": _number(close.rolling(200, min_periods=200).mean().iloc[-1]) if len(close) >= 200 else None,
        "ema20": float(close.ewm(span=20, adjust=False).mean().iloc[-1]),
        "ema50": float(close.ewm(span=50, adjust=False).mean().iloc[-1]),
        "rsi14": _number(rsi_series.iloc[-1]),
        "macd": _number(macd_line.iloc[-1]),
        "macd_signal": _number(macd_signal.iloc[-1]),
        "adx14": _number(adx_series.iloc[-1]),
        "atr14": _number(atr14_series.iloc[-1]),
        "relative_volume": latest_volume / average_volume if average_volume > 0 else None,
        "average_volume_20d": average_volume if average_volume > 0 else None,
        "latest_volume": latest_volume,
        "vwap": _number(full_vwap.iloc[-1]),
        "anchored_vwap_20d": _number(anchored_vwap),
        "support_20d": float(low.iloc[-21:-1].min()) if len(low) > 21 else float(low.min()),
        "resistance_20d": float(high.iloc[-21:-1].max()) if len(high) > 21 else float(high.max()),
        "support_50d": float(low.iloc[-50:].min()) if len(low) >= 50 else float(low.min()),
        "resistance_50d": float(high.iloc[-50:].max()) if len(high) >= 50 else float(high.max()),
        "high_60d": float(high.iloc[-60:].max()) if len(high) >= 60 else float(high.max()),
        "low_60d": float(low.iloc[-60:].min()) if len(low) >= 60 else float(low.min()),
        "weekly_close": _number(weekly_close.iloc[-1]) if len(weekly_close) else None,
        "weekly_ema10": _number(weekly_close.ewm(span=10, adjust=False).mean().iloc[-1]) if len(weekly_close) >= 10 else None,
        "weekly_ema20": _number(weekly_close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(weekly_close) >= 20 else None,
        "weekly_return_13w_pct": _return_pct(weekly_close, 13),
        "bar_as_of": str(last_date),
        "history_bars": len(close),
    }


def _index_snapshot(frame: pd.DataFrame | None) -> dict[str, Any] | None:
    metrics = _technical_snapshot(frame) if frame is not None else None
    if not metrics:
        return None
    close = metrics["close"]
    return {
        "last": _round(close),
        "change_1d_pct": _round(metrics["daily_change_pct"]),
        "change_5d_pct": _round(metrics["return_5d_pct"]),
        "change_20d_pct": _round(metrics["return_20d_pct"]),
        "above_20dma": close > metrics["sma20"] if metrics["sma20"] is not None else None,
        "above_50dma": close > metrics["sma50"] if metrics["sma50"] is not None else None,
        "as_of": metrics["bar_as_of"],
    }


def _market_regime(
    benchmarks: dict[str, pd.DataFrame],
    stock_metrics: dict[str, dict[str, Any]],
    breadth_index: str = "NIFTY 50",
) -> tuple[dict[str, Any], float | None]:
    indices = {
        label: _index_snapshot(benchmarks.get(ticker))
        for label, ticker in BENCHMARKS.items()
    }
    nifty = indices.get("NIFTY 50")
    selected_benchmark = indices.get(breadth_index) or nifty
    bank = indices.get("NIFTY BANK")
    vix_frame = benchmarks.get(BENCHMARKS["INDIA VIX"])
    vix_metrics = _technical_snapshot(vix_frame) if vix_frame is not None else None

    advancing = declining = unchanged = above20 = above50 = 0
    breadth_count = 0
    daily_returns = []
    for metrics in stock_metrics.values():
        change = metrics.get("daily_change_pct")
        if change is not None:
            breadth_count += 1
            daily_returns.append(change)
            if change > 0:
                advancing += 1
            elif change < 0:
                declining += 1
            else:
                unchanged += 1
        if metrics.get("sma20") is not None:
            above20 += metrics["close"] > metrics["sma20"]
        if metrics.get("sma50") is not None:
            above50 += metrics["close"] > metrics["sma50"]

    breadth20_count = sum(1 for row in stock_metrics.values() if row.get("sma20") is not None)
    breadth50_count = sum(1 for row in stock_metrics.values() if row.get("sma50") is not None)
    pct_above20 = 100 * above20 / breadth20_count if breadth20_count else None
    pct_above50 = 100 * above50 / breadth50_count if breadth50_count else None
    adv_dec_ratio = advancing / declining if declining else (float(advancing) if advancing else None)

    score = 0
    factors = []
    if selected_benchmark:
        for key, period in (("above_20dma", 20), ("above_50dma", 50)):
            value = selected_benchmark.get(key)
            if value is not None:
                score += 2 if value else -2
                factors.append(f"{breadth_index} is {'above' if value else 'below'} its {period}-day average")
    if bank:
        for key, label in (("above_20dma", "Bank NIFTY 20 DMA"), ("above_50dma", "Bank NIFTY 50 DMA")):
            value = bank.get(key)
            if value is not None:
                score += 1 if value else -1
                factors.append(f"{label} is {'supportive' if value else 'weak'}")
    if pct_above20 is not None:
        if pct_above20 >= 70:
            score += 2
        elif pct_above20 <= 30:
            score -= 2
        factors.append(f"{pct_above20:.0f}% of the {len(stock_metrics)} fetched {breadth_index} stocks are above their 20 DMA")
    if vix_metrics and vix_metrics.get("return_5d_pct") is not None:
        vix_change = vix_metrics["return_5d_pct"]
        if vix_change <= 0:
            score += 1
        elif vix_change >= 10:
            score -= 1
        factors.append(f"India VIX changed {vix_change:+.1f}% over five sessions")

    if not selected_benchmark:
        regime = "UNAVAILABLE"
    elif score >= 6:
        regime = "STRONG BULLISH"
    elif score >= 3:
        regime = "BULLISH"
    elif score >= 0:
        regime = "NEUTRAL"
    elif score >= -3:
        regime = "CAUTIOUS"
    elif score >= -5:
        regime = "BEARISH"
    else:
        regime = "STRONG BEARISH"

    global_cues = {}
    for label, ticker in GLOBAL_MARKETS.items():
        item = _index_snapshot(benchmarks.get(ticker))
        global_cues[label] = item or {"status": "unavailable"}

    report = {
        "classification": regime,
        "score": score if selected_benchmark else None,
        "benchmark": breadth_index,
        "rationale": factors if factors else ["Insufficient benchmark data to classify market regime."],
        "indices": indices,
        "india_vix": ({
            "last": _round(vix_metrics["close"]),
            "change_5d_pct": _round(vix_metrics["return_5d_pct"]),
            "as_of": vix_metrics["bar_as_of"],
        } if vix_metrics else {"status": "unavailable"}),
        "breadth": {
            "advances": advancing,
            "declines": declining,
            "unchanged": unchanged,
            "advance_decline_ratio": _round(adv_dec_ratio),
            "above_20dma_pct": _round(pct_above20),
            "above_50dma_pct": _round(pct_above50),
            "sample_count": breadth_count,
        },
        "fii_dii": {
            "status": "provider_required",
            "reason": "An authorized FII/DII data feed is required; automated collection from NSE's public website is not enabled.",
        },
        "global_cues": global_cues,
    }
    benchmark_return = selected_benchmark.get("change_20d_pct") if selected_benchmark else None
    return report, benchmark_return


def _sector_rotation(
    metadata: list[dict[str, str]], stock_metrics: dict[str, dict[str, Any]], nifty_return_20d: float | None
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in metadata:
        symbol = item["symbol"].upper()
        metrics = stock_metrics.get(symbol)
        industry = str(item.get("industry") or "Unknown")
        if metrics and industry.lower() != "unknown":
            grouped.setdefault(industry, []).append(metrics)

    sectors = []
    for industry, members in grouped.items():
        returns20 = [row["return_20d_pct"] for row in members if row.get("return_20d_pct") is not None]
        returns60 = [row["return_60d_pct"] for row in members if row.get("return_60d_pct") is not None]
        if len(returns20) < 2:
            continue
        avg20 = sum(returns20) / len(returns20)
        avg60 = sum(returns60) / len(returns60) if returns60 else None
        sectors.append({
            "sector": industry,
            "sample_size": len(returns20),
            "return_20d_pct": _round(avg20),
            "return_60d_pct": _round(avg60),
            "relative_20d_pct": _round(avg20 - nifty_return_20d) if nifty_return_20d is not None else None,
            "sample_note": "Industry group measured from available selected-index constituents, not a full sector index.",
        })
    return sorted(sectors, key=lambda row: row["return_20d_pct"] or -999, reverse=True)


def _candidate_pattern(
    timeframe: str,
    frame: pd.DataFrame,
    metrics: dict[str, Any],
    min_rr: float,
) -> dict[str, Any] | None:
    close = metrics["close"]
    atr14 = metrics.get("atr14") or close * 0.015
    if timeframe == "short":
        setup = compute_setup(frame, min_rr=min_rr)
        if not setup:
            return None
        return {
            "direction": "LONG" if setup.setup_type == "breakout" else "SHORT",
            "setup": setup.setup_type.title(),
            "entry": setup.entry,
            "stop": setup.stop,
            "target_1": setup.target_1,
            "target_2": setup.target_2,
            "target_3": setup.entry + (1 if setup.setup_type == "breakout" else -1) * abs(setup.entry - setup.stop) * 4,
            "rr": setup.rr,
            "confirmation": "Daily close beyond the previous 20-session range with volume at least 1.2x the previous 20-session average.",
            "price_action_points": 15,
        }

    direction = 0
    setup_label = ""
    confirmation = ""
    price_action_points = 0
    if timeframe == "weekly":
        prior_high = float(frame["High"].iloc[-21:-1].max()) if len(frame) > 21 else float(frame["High"].max())
        prior_low = float(frame["Low"].iloc[-21:-1].min()) if len(frame) > 21 else float(frame["Low"].min())
        volume_ratio = metrics.get("relative_volume") or 0
        uptrend = metrics.get("sma50") is not None and close > metrics["sma50"] and metrics["ema20"] > metrics["sma50"]
        downtrend = metrics.get("sma50") is not None and close < metrics["sma50"] and metrics["ema20"] < metrics["sma50"]
        weekly_up = metrics.get("weekly_ema10") is not None and metrics.get("weekly_ema20") is not None and metrics["weekly_ema10"] > metrics["weekly_ema20"]
        weekly_down = metrics.get("weekly_ema10") is not None and metrics.get("weekly_ema20") is not None and metrics["weekly_ema10"] < metrics["weekly_ema20"]
        near_ema = abs(close - metrics["ema20"]) <= 1.5 * atr14
        if close > prior_high and volume_ratio >= 1.0:
            direction, setup_label, price_action_points = 1, "20-session breakout", 15
            confirmation = "Daily close above the prior 20-session high; confirm with volume at or above its 20-session average."
        elif close < prior_low and volume_ratio >= 1.0:
            direction, setup_label, price_action_points = -1, "20-session breakdown", 15
            confirmation = "Daily close below the prior 20-session low; confirm with volume at or above its 20-session average."
        elif uptrend and weekly_up and near_ema and close > metrics["previous_close"]:
            direction, setup_label, price_action_points = 1, "Rising-trend pullback", 12
            confirmation = "Wait for a daily bullish reversal near the rising 20 EMA; do not enter while price is falling."
        elif downtrend and weekly_down and near_ema and close < metrics["previous_close"]:
            direction, setup_label, price_action_points = -1, "Falling-trend pullback", 12
            confirmation = "Wait for a daily bearish reversal near the falling 20 EMA; do not enter while price is rising."
        elif uptrend and weekly_up and (metrics.get("return_20d_pct") or 0) > 0:
            direction, setup_label, price_action_points = 1, "Trend continuation", 10
            confirmation = "Require price to hold above the 20 EMA and the weekly trend to remain positive."
        elif downtrend and weekly_down and (metrics.get("return_20d_pct") or 0) < 0:
            direction, setup_label, price_action_points = -1, "Downtrend continuation", 10
            confirmation = "Require price to stay below the 20 EMA and the weekly trend to remain negative."
    else:
        required = (metrics.get("sma50"), metrics.get("sma100"), metrics.get("sma200"))
        if all(value is not None for value in required):
            sma50, sma100, sma200 = required
            near_50 = abs(close - sma50) <= 1.5 * atr14
            extended_high = close >= metrics["high_60d"] * 0.95
            if close > sma200 and sma50 > sma100 > sma200 and (metrics.get("return_60d_pct") or 0) > 0 and extended_high:
                direction, setup_label, price_action_points = 1, "Major-base continuation", 15
                confirmation = "Wait for a weekly close above the 60-session base or a successful retest of the breakout level."
            elif close < sma200 and sma50 < sma100 < sma200 and (metrics.get("return_60d_pct") or 0) < 0 and close <= metrics["low_60d"] * 1.05:
                direction, setup_label, price_action_points = -1, "Major-base breakdown", 15
                confirmation = "Require a weekly close below the 60-session base; short exposure needs derivatives and verified margin data."
            elif close > sma200 and sma50 > sma200 and near_50 and close > metrics["previous_close"]:
                direction, setup_label, price_action_points = 1, "Rising 50 DMA retest", 12
                confirmation = "Wait for a bullish reversal near the rising 50 DMA; confirm the weekly trend remains intact."
            elif close < sma200 and sma50 < sma200 and near_50 and close < metrics["previous_close"]:
                direction, setup_label, price_action_points = -1, "Falling 50 DMA retest", 12
                confirmation = "Wait for a bearish reversal near the falling 50 DMA; short exposure needs derivatives."

    if not direction:
        return None

    risk_distance = max(1.2 * atr14, close * 0.005)
    swing_lookback = 20 if timeframe == "weekly" else 50
    swing_low = float(frame["Low"].iloc[-swing_lookback:].min())
    swing_high = float(frame["High"].iloc[-swing_lookback:].max())
    stop = min(swing_low, close - risk_distance) if direction > 0 else max(swing_high, close + risk_distance)
    risk_distance = abs(close - stop)
    structural_target = metrics.get("resistance_50d") if direction > 0 else metrics.get("support_50d")
    structural_distance = abs(structural_target - close) if structural_target is not None and (structural_target - close) * direction > 0 else 0
    target_1_distance = max(2 * risk_distance, structural_distance)
    target_2_distance = max(3 * risk_distance, target_1_distance + risk_distance)
    target_3_distance = max(4 * risk_distance, target_2_distance + risk_distance)
    return {
        "direction": "LONG" if direction > 0 else "SHORT",
        "setup": setup_label,
        "entry": close,
        "stop": stop,
        "target_1": close + direction * target_1_distance,
        "target_2": close + direction * target_2_distance,
        "target_3": close + direction * target_3_distance,
        "rr": target_2_distance / risk_distance if risk_distance else 0,
        "confirmation": confirmation,
        "price_action_points": price_action_points,
    }


def _ai_candidate_pattern(
    timeframe: str,
    metrics: dict[str, Any],
    analog: dict[str, Any],
    min_rr: float,
) -> dict[str, Any] | None:
    """Build spot-level research levels for a qualified historical analog."""
    direction = 1 if analog.get("direction") == "LONG" else -1 if analog.get("direction") == "SHORT" else 0
    close = _number(metrics.get("close"))
    atr14 = _number(metrics.get("atr14"))
    if not direction or close is None or close <= 0:
        return None
    risk_distance = max(1.5 * (atr14 or close * 0.015), close * 0.0075)
    stop = close - direction * risk_distance
    target_1_distance = 2 * risk_distance
    target_2_distance = max(min_rr, 3.0) * risk_distance
    target_3_distance = max(4.0, min_rr + 1.0) * risk_distance
    horizon = HORIZONS[timeframe]
    win_rate = analog["historical_positive_rate_pct"]
    average_return = analog["average_forward_return_pct"]
    confirmation = (
        f"The analog model matched {analog['analog_samples']} completed historical windows "
        f"from {analog['distinct_symbols']} F&O stocks over {horizon} sessions. "
        f"{win_rate:.1f}% were positive; weighted average return was {average_return:+.2f}%. "
        "Historical outcomes are not a forecast. Confirm with a fresh Kotak quote, price structure, and volume."
    )
    return {
        "direction": "LONG" if direction > 0 else "SHORT",
        "setup": f"AI analog pattern · {analog['direction']}",
        "entry": close,
        "stop": stop,
        "target_1": close + direction * target_1_distance,
        "target_2": close + direction * target_2_distance,
        "target_3": close + direction * target_3_distance,
        "rr": target_2_distance / risk_distance,
        "confirmation": confirmation,
        "price_action_points": round(15 * analog["match_strength"] / 100),
        "ai_pattern": {
            **analog,
            "timeframe": timeframe,
            "entry_reference": close,
            "stop_distance_atr": round(risk_distance / atr14, 2) if atr14 and atr14 > 0 else None,
        },
    }


def _market_points(direction: str, classification: str) -> int:
    bullish = {"STRONG BULLISH": 10, "BULLISH": 8, "NEUTRAL": 5, "CAUTIOUS": 2, "BEARISH": 0, "STRONG BEARISH": 0}
    if classification not in bullish:
        return 0
    points = bullish.get(classification, 0)
    return points if direction == "LONG" else 10 - points


def _setup_score(
    direction: str,
    setup: str,
    metrics: dict[str, Any],
    sector: dict[str, Any] | None,
    regime: str,
    rr: float,
    price_action_points: int,
) -> tuple[int, dict[str, int]]:
    sign = 1 if direction == "LONG" else -1
    technical = 0
    for left, right, points in (
        (metrics.get("close"), metrics.get("sma20"), 6),
        (metrics.get("ema20"), metrics.get("sma50"), 6),
        (metrics.get("sma50"), metrics.get("sma100"), 6),
        (metrics.get("sma100"), metrics.get("sma200"), 7),
    ):
        if left is not None and right is not None and (left - right) * sign > 0:
            technical += points

    relative_strength = metrics.get("relative_strength_20d_pct")
    rs_points = 10 if relative_strength is not None and relative_strength >= 5 else 8 if relative_strength is not None and relative_strength >= 2 else 5 if relative_strength is not None and relative_strength >= 0 else 2 if relative_strength is not None and relative_strength > -3 else 0
    if direction == "SHORT" and relative_strength is not None:
        rs_points = 10 - rs_points

    volume_ratio = metrics.get("relative_volume")
    volume_points = min(10, max(0, round((volume_ratio - 0.75) * 8))) if volume_ratio is not None else 0
    sector_relative = sector.get("relative_20d_pct") if sector else None
    sector_points = 10 if sector_relative is not None and sector_relative >= 3 else 7 if sector_relative is not None and sector_relative > 0 else 5 if sector_relative is not None and sector_relative >= -2 else 2 if sector_relative is not None else 0
    if direction == "SHORT" and sector_relative is not None:
        sector_points = 10 - sector_points
    rr_points = 10 if rr >= 3 else 8 if rr >= 2.5 else 6 if rr >= 2 else 0

    breakdown = {
        "Technical structure / 25": technical,
        "Price action / 15": price_action_points,
        "Volume / 10": volume_points,
        "Relative strength / 10": rs_points,
        "Sector strength / 10": sector_points,
        "Fundamentals and catalyst / 10 (unavailable)": 0,
        "Market regime / 10": _market_points(direction, regime),
        "Risk/reward / 10": rr_points,
    }
    return sum(breakdown.values()), breakdown


def _classify(score: int, complete: bool = False) -> tuple[str, str]:
    if score >= 85 and complete:
        return "A+", "MEDIUM"
    if score >= 75:
        return "A", "MEDIUM"
    if score >= 60:
        return "B", "LOW"
    return "C", "LOW"


def _make_candidate(
    symbol: str,
    timeframe: str,
    metadata: dict[str, str],
    frame: pd.DataFrame,
    metrics: dict[str, Any],
    pattern: dict[str, Any],
    sector: dict[str, Any] | None,
    regime: str,
    ltp_by_symbol: dict[str, float],
    capital: float,
    risk_pct: float,
) -> dict[str, Any] | None:
    entry, stop = float(pattern["entry"]), float(pattern["stop"])
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return None
    rr = float(pattern["rr"])
    direction = str(pattern["direction"])
    current_price = _number(ltp_by_symbol.get(symbol))
    trigger_confirmed = None if current_price is None else (
        current_price >= entry if direction == "LONG" else current_price <= entry
    )

    points, breakdown = _setup_score(
        direction, str(pattern["setup"]), metrics, sector, regime, rr, int(pattern["price_action_points"])
    )
    quality, confidence = _classify(points, complete=False)
    if pattern.get("ai_pattern"):
        confidence = "LOW"
    risk_budget = capital * risk_pct / 100
    quantity = max(0, math.floor(risk_budget / risk_per_share))
    capital_required = quantity * entry if direction == "LONG" else None
    if direction == "LONG" and entry > 0:
        quantity = min(quantity, math.floor(capital / entry))
        capital_required = quantity * entry
    elif direction == "SHORT":
        # Do not imply a usable cash-share size without derivatives and margin data.
        quantity = 0
    maximum_loss = quantity * risk_per_share
    if direction == "SHORT":
        maximum_loss = None

    risk_flags = []
    if metrics.get("relative_volume") is not None and metrics["relative_volume"] >= 4 and abs(metrics.get("daily_change_pct") or 0) >= 7:
        risk_flags.append("Abnormal price and volume expansion; verify the event before acting.")
    if metrics.get("ema20") and direction == "LONG" and entry > metrics["ema20"] * 1.10:
        risk_flags.append("Price is more than 10% above its 20 EMA; avoid chasing an extended move.")
    if quantity < 1 and direction == "LONG":
        risk_flags.append("Account capital/risk settings do not permit one share at the calculated stop distance.")
    risk_flags.append("Official filings, catalysts, and corporate-event risks are not verified.")
    if pattern.get("ai_pattern"):
        risk_flags.append("AI pattern evidence is based on historical analogs and has not been validated as a live trading edge.")
    if direction == "SHORT":
        risk_flags.append("Multi-day short exposure requires derivatives; derivatives and margin are not connected to this scanner.")

    if current_price is None:
        action = "NO TRADE"
    elif quality == "C":
        action = "AVOID"
    elif direction == "SHORT":
        action = "WATCH"
    elif not trigger_confirmed or regime in {"CAUTIOUS", "BEARISH", "STRONG BEARISH", "UNAVAILABLE"}:
        action = "WATCH"
    elif risk_flags or quality not in {"A+", "A"}:
        action = "WATCH"
    elif timeframe == "weekly" and "pullback" in str(pattern["setup"]).lower():
        action = "BUY ON RETEST"
    else:
        action = "BUY ON CONFIRMATION"

    technical_reason = (
        f"{pattern['setup']}; close {entry:.2f}, RSI(14) {_round(metrics.get('rsi14'))}, "
        f"MACD {'positive' if (metrics.get('macd') or 0) > (metrics.get('macd_signal') or 0) else 'negative'}, "
        f"ADX(14) {_round(metrics.get('adx14'))}, ATR(14) {_round(metrics.get('atr14'))}, "
        f"20-session relative volume {_round(metrics.get('relative_volume'))}x."
    )
    if sector:
        sector_strength = f"{sector['sector']}: {sector['return_20d_pct']:+.2f}% over 20 sessions; {sector['relative_20d_pct']:+.2f}% versus NIFTY 50."
    else:
        sector_strength = "Industry group or sector comparison unavailable."

    timeframe_name = {"short": "1–2 trading days", "weekly": "3–10 trading days", "monthly": "2–8 weeks"}[timeframe]
    if direction == "SHORT":
        entry_type = "Wait for breakdown confirmation; no cash-equity short recommendation is made."
    elif "pullback" in str(pattern["setup"]).lower() or "retest" in str(pattern["setup"]).lower():
        entry_type = "Buy on retest only after a bullish reversal near the stated support/average."
    else:
        entry_type = "Buy only after the specified close/volume confirmation; do not anticipate the breakout."

    yahoo_symbol = f"{symbol}.NS"
    return {
        "symbol": symbol,
        "company": metadata.get("name", symbol),
        "sector": metadata.get("industry", "Unknown"),
        "timeframe": timeframe_name,
        "direction": direction,
        "setup": pattern["setup"],
        "ai_pattern": pattern.get("ai_pattern"),
        "current_price": _round(current_price),
        "last_daily_close": _round(metrics["close"]),
        "daily_bar_as_of": metrics["bar_as_of"],
        "entry": _round(entry),
        "entry_zone_low": _round(entry - metrics["atr14"] * 0.15 if direction == "LONG" and metrics.get("atr14") else entry),
        "entry_zone_high": _round(entry + metrics["atr14"] * 0.15 if direction == "LONG" and metrics.get("atr14") else entry),
        "entry_type": entry_type,
        "confirmation": pattern["confirmation"],
        "stop_loss": _round(stop),
        "target_1": _round(pattern["target_1"]),
        "target_2": _round(pattern["target_2"]),
        "target_3": _round(pattern["target_3"]),
        "risk_reward": _round(rr),
        "risk_reward_by_target": {
            "target_1": _round(abs(pattern["target_1"] - entry) / risk_per_share),
            "target_2": _round(abs(pattern["target_2"] - entry) / risk_per_share),
            "target_3": _round(abs(pattern["target_3"] - entry) / risk_per_share),
        },
        "risk_budget_rupees": _round(risk_budget),
        "position_quantity": quantity,
        "capital_required_rupees": _round(capital_required),
        "maximum_loss_rupees": _round(maximum_loss),
        "risk_pct_of_capital": risk_pct,
        "setup_score": points,
        "score_breakdown": breakdown,
        "score_note": (
            "The technical composite uses fixed rules; the separate AI analog strength summarizes similarity and historical outcome agreement. Neither is a calibrated probability. Fundamentals/catalyst are unverified and score zero."
            if pattern.get("ai_pattern") else
            "Fundamentals/catalyst are unverified and score zero; total is conservative and incomplete."
        ),
        "quality": quality,
        "confidence": confidence,
        "action": action,
        "live_trigger_confirmed": trigger_confirmed,
        "technical_reason": technical_reason,
        "fundamental_reason": "Not verified. Check the latest company results and investor-relations filings before any trade.",
        "fundamental_metrics": {"status": "unavailable", "source": None},
        "sector_strength": sector_strength,
        "volume_interpretation": f"Latest daily volume is {_round(metrics.get('relative_volume'))}x its previous 20-session average; daily volume is not delivery volume.",
        "derivatives": {"status": "unavailable", "reason": "Kotak futures/options quote and OI checks have not run for this candidate."},
        "delivery": {
            "status": "provider_required",
            "reason": "NSE delivery positions are end-of-day data; an authorized feed or user-supplied report is required.",
        },
        "corporate_events": {
            "status": "unavailable",
            "risk_flagged": False,
            "items": [],
            "reason": "No recent headline or earnings-date context was returned by the connected provider.",
        },
        "news": {"status": "unavailable", "reason": "No current news source is connected; catalyst claims are not inferred."},
        "support": _round(metrics.get("support_20d")),
        "resistance": _round(metrics.get("resistance_20d")),
        "technical_indicators": {
            key: _round(metrics.get(key))
            for key in ("sma20", "sma50", "sma100", "sma200", "ema20", "rsi14", "macd", "macd_signal", "adx14", "atr14", "vwap", "anchored_vwap_20d")
        },
        "relative_strength_20d_pct": _round(metrics.get("relative_strength_20d_pct")),
        "risk_flags": risk_flags,
        "invalidating_condition": f"Exit/avoid if price violates the stop at ₹{stop:.2f}; reassess if the stated breakout/retest condition fails.",
        "trade_management": {
            "at_1r": "Consider partial profit only after the setup remains valid; trail the stop below/above a confirmed swing.",
            "at_2r": "Protect gains with a structure-based trailing stop; do not widen the original stop.",
            "sideways": "Reduce or exit if the setup loses momentum within its stated holding period.",
            "gap_against": "Do not average down; reassess at the open and respect the planned maximum loss.",
            "target_reached": "Book according to the plan and trail only while structure supports continuation.",
        },
        "source_dates": {
            "ohlcv_source": "Yahoo Finance chart data",
            "ohlcv_bar_date": metrics["bar_as_of"],
            "current_price_source": "Kotak Neo quote snapshot" if current_price is not None else "unavailable",
            "signal_generated_at": datetime.now(INDIA_TZ).isoformat(timespec="seconds"),
            "history_url": f"https://finance.yahoo.com/quote/{yahoo_symbol}/history/",
        },
    }


def _apply_candidate_quote(candidate: dict[str, Any], price: float | None, regime: str) -> None:
    """Attach a Kotak LTP and re-evaluate the display action without calling it confirmation."""
    current_price = _number(price)
    candidate["current_price"] = _round(current_price)
    candidate["live_trigger_confirmed"] = None if current_price is None else (
        current_price >= candidate["entry"] if candidate["direction"] == "LONG"
        else current_price <= candidate["entry"]
    )
    candidate["source_dates"]["current_price_source"] = "Kotak Neo quote snapshot" if current_price is not None else "unavailable"

    if current_price is None:
        candidate["action"] = "NO TRADE"
    elif candidate["quality"] == "C":
        candidate["action"] = "AVOID"
    elif candidate["direction"] == "SHORT":
        candidate["action"] = "WATCH"
    elif not candidate["live_trigger_confirmed"] or regime in {"CAUTIOUS", "BEARISH", "STRONG BEARISH", "UNAVAILABLE"}:
        candidate["action"] = "WATCH"
    elif candidate["risk_flags"] or candidate["quality"] not in {"A+", "A"}:
        candidate["action"] = "WATCH"
    elif candidate["timeframe"] == "3–10 trading days" and "pullback" in candidate["setup"].lower():
        candidate["action"] = "BUY ON RETEST"
    else:
        candidate["action"] = "BUY ON CONFIRMATION"


def _avoid_flags(stock_metrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    flagged = []
    for symbol, metrics in stock_metrics.items():
        reasons = []
        day_change = metrics.get("daily_change_pct")
        if day_change is not None and abs(day_change) >= 10:
            reasons.append(f"One-session move of {day_change:+.1f}%; avoid chasing without a verified catalyst and retest.")
        ema20 = metrics.get("ema20")
        if ema20 and abs(metrics["close"] / ema20 - 1) >= 0.10:
            reasons.append("Price is at least 10% away from its 20 EMA and may be extended.")
        if reasons:
            flagged.append({"symbol": symbol, "last_daily_close": _round(metrics["close"]), "reasons": reasons, "bar_as_of": metrics["bar_as_of"]})
    return sorted(flagged, key=lambda row: abs(stock_metrics[row["symbol"]].get("daily_change_pct") or 0), reverse=True)[:10]


def _publish_date(value: Any) -> str | None:
    try:
        if isinstance(value, (int, float)):
            published = datetime.fromtimestamp(value, tz=timezone.utc)
        elif isinstance(value, str) and value.strip():
            published = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        else:
            return None
        return published.astimezone(INDIA_TZ).isoformat(timespec="minutes")
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _news_item(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    content = item.get("content") if isinstance(item.get("content"), dict) else item
    title = str(content.get("title") or item.get("title") or "").strip()
    provider = content.get("provider") or item.get("publisher") or {}
    publisher = provider.get("displayName") if isinstance(provider, dict) else str(provider)
    url_data = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
    url = url_data.get("url") if isinstance(url_data, dict) else None
    url = url or content.get("link") or item.get("link")
    published = _publish_date(content.get("pubDate") or item.get("providerPublishTime") or item.get("published_at"))
    if not title or not url or not str(url).startswith("http") or not published:
        return None
    cutoff = datetime.now(INDIA_TZ) - timedelta(days=45)
    if datetime.fromisoformat(published).astimezone(INDIA_TZ) < cutoff:
        return None
    return {"title": title, "publisher": str(publisher or "Publisher unavailable"), "published_at": published, "url": str(url)}


def _fetch_company_context(symbol: str) -> dict[str, Any]:
    """Fetch source-linked Yahoo Finance summary metrics and recent headlines."""
    if yf is None:
        return {"fundamental_metrics": None, "news": []}
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        raw_info = ticker.get_info()
        info = raw_info if isinstance(raw_info, dict) else {}
    except Exception as exc:
        logger.info("Yahoo Finance fundamentals unavailable for %s: %s", symbol, exc)
        info = {}

    metrics = None
    if info:
        metrics = {
            "status": "available",
            "source": "Yahoo Finance aggregated company data",
            "source_url": f"https://finance.yahoo.com/quote/{symbol}.NS/financials/",
            "as_of": datetime.now(INDIA_TZ).isoformat(timespec="minutes"),
            "market_cap": _number(info.get("marketCap")),
            "trailing_pe": _number(info.get("trailingPE")),
            "revenue_growth_pct": _round((info.get("revenueGrowth") or 0) * 100) if info.get("revenueGrowth") is not None else None,
            "earnings_growth_pct": _round((info.get("earningsGrowth") or 0) * 100) if info.get("earningsGrowth") is not None else None,
            "profit_margin_pct": _round((info.get("profitMargins") or 0) * 100) if info.get("profitMargins") is not None else None,
            "return_on_equity_pct": _round((info.get("returnOnEquity") or 0) * 100) if info.get("returnOnEquity") is not None else None,
            "earnings_timestamp": _publish_date(info.get("earningsTimestamp")),
            "ex_dividend_date": _publish_date(info.get("exDividendDate")),
            "dividend_date": _publish_date(info.get("dividendDate")),
            "verification_note": "Aggregated provider data; verify figures and earnings dates against the latest company filing.",
        }

    articles: list[dict[str, str]] = []
    try:
        for tab in ("news", "press releases"):
            raw_items = ticker.get_news(count=8, tab=tab)
            if isinstance(raw_items, dict):
                raw_items = raw_items.get("news", [])
            for item in raw_items or []:
                parsed = _news_item(item)
                if parsed and all(existing["url"] != parsed["url"] for existing in articles):
                    articles.append(parsed)
        articles.sort(key=lambda item: item["published_at"], reverse=True)
    except Exception as exc:
        logger.info("Yahoo Finance news unavailable for %s: %s", symbol, exc)

    return {"fundamental_metrics": metrics, "news": articles[:5]}


def _fetch_company_contexts(symbols: list[str]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    if not symbols:
        return contexts
    with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as executor:
        futures = {executor.submit(_fetch_company_context, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                contexts[symbol] = future.result()
            except Exception as exc:
                logger.info("Company research context unavailable for %s: %s", symbol, exc)
                contexts[symbol] = {"fundamental_metrics": None, "news": []}
    return contexts


def _apply_company_context(
    candidate: dict[str, Any], context: dict[str, Any], imported_events: list[dict[str, Any]] | None = None,
) -> None:
    metrics = context.get("fundamental_metrics")
    news = context.get("news") or []
    candidate["fundamental_metrics"] = metrics or {"status": "unavailable", "source": None}
    candidate["news"] = {
        "status": "available" if news else "unavailable",
        "reason": "Headline only; review the linked publisher source before treating it as a catalyst." if news else "No dated recent headline was returned by the connected provider.",
        "items": news,
    }
    corporate_items: list[dict[str, str]] = []
    risk_flagged = False
    today = datetime.now(INDIA_TZ).date()
    for field, label in (
        ("earnings_timestamp", "Earnings date"),
        ("ex_dividend_date", "Ex-dividend date"),
        ("dividend_date", "Dividend date"),
    ):
        value = (metrics or {}).get(field)
        if not value:
            continue
        try:
            event_day = datetime.fromisoformat(str(value)).date()
        except ValueError:
            continue
        if 0 <= (event_day - today).days <= 30:
            corporate_items.append({"event": label, "date": event_day.isoformat(), "source": "Yahoo Finance aggregated calendar"})
            if (event_day - today).days <= 7:
                risk_flagged = True

    event_keywords = (
        "earnings", "quarterly results", "board meeting", "dividend", "stock split", "bonus issue",
        "rights issue", "merger", "acquisition", "demerger", "fraud", "investigation", "probe",
        "default", "insolvency", "pledge", "resignation", "penalty", "regulatory action",
        "show cause", "auditor", "credit rating downgrade", "cancellation",
    )
    event_headlines = []
    event_news_cutoff = datetime.now(INDIA_TZ) - timedelta(days=14)
    for item in news:
        title = str(item.get("title") or "")
        matched = [word for word in event_keywords if word in title.lower()]
        try:
            published_at = datetime.fromisoformat(str(item.get("published_at") or ""))
        except ValueError:
            published_at = None
        if matched and published_at and published_at >= event_news_cutoff:
            event_headlines.append({
                "event": title,
                "date": str(item.get("published_at") or ""),
                "source": str(item.get("publisher") or "News publisher"),
                "url": str(item.get("url") or ""),
                "matched_terms": ", ".join(matched[:3]),
            })
    if event_headlines:
        corporate_items.extend(event_headlines[:5])
        risk_flagged = True
    imported_events = imported_events or []
    corporate_items.extend(imported_events)
    for event in imported_events:
        try:
            event_day = date.fromisoformat(str(event.get("date") or ""))
        except ValueError:
            continue
        if 0 <= (event_day - today).days <= 7:
            risk_flagged = True
    has_context = bool(metrics or news or imported_events)
    candidate["corporate_events"] = {
        "status": "available" if imported_events else "partial" if has_context else "unavailable",
        "provider": "Uploaded event CSV and Yahoo Finance aggregated calendar/headlines" if imported_events else "Yahoo Finance aggregated calendar and headlines",
        "risk_flagged": risk_flagged,
        "items": corporate_items[:8],
        "reason": (
            "Uploaded event data is shown with its source link when supplied; Yahoo dates/headline matches are preliminary. Confirm the issuer/exchange filing before acting."
            if has_context else "No recent headline or earnings-date context was returned by the connected provider."
        ),
    }
    if metrics:
        candidate["fundamental_reason"] = "Aggregated financial metrics are shown below; confirm the latest quarterly results and company disclosures before acting."

    fundamental_points = 0
    if metrics:
        if (metrics.get("revenue_growth_pct") or 0) > 0:
            fundamental_points += 2
        if (metrics.get("earnings_growth_pct") or 0) > 0:
            fundamental_points += 2
        if (metrics.get("profit_margin_pct") or 0) > 0:
            fundamental_points += 2
        if (metrics.get("return_on_equity_pct") or 0) > 0:
            fundamental_points += 1
    score_key = "Fundamentals and catalyst / 10 (catalyst partial)"
    old_key = "Fundamentals and catalyst / 10 (unavailable)"
    candidate["score_breakdown"].pop(old_key, None)
    candidate["score_breakdown"][score_key] = fundamental_points
    candidate["setup_score"] = sum(candidate["score_breakdown"].values())
    candidate["score_note"] = (
        "Fundamental points use available aggregated financial metrics only; catalyst verification and official filing checks are incomplete. The separate AI analog strength is not a calibrated probability."
        if candidate.get("ai_pattern") else
        "Fundamental points use available aggregated financial metrics only; catalyst verification and official filing checks are incomplete. Missing data receives zero points."
    )
    candidate["quality"], candidate["confidence"] = _classify(candidate["setup_score"], complete=False)
    if candidate.get("ai_pattern"):
        candidate["confidence"] = "LOW"
    candidate["risk_flags"] = [
        flag for flag in candidate["risk_flags"]
        if "Official filings, catalysts, and corporate-event risks are not verified" not in flag
    ]
    if metrics or news:
        candidate["risk_flags"].append("Provider data is aggregated; verify fundamentals, headlines, and corporate events against official company/exchange disclosures before acting.")
    elif imported_events:
        candidate["risk_flags"].append("Corporate events were imported from a user-supplied report; verify the source and event details before acting.")
    else:
        candidate["risk_flags"].append("Fundamentals, current headlines, and corporate-event checks are unavailable; this remains a watchlist signal.")
    if risk_flagged:
        candidate["risk_flags"].append("Potential upcoming corporate event or event-related headline detected; verify the official filing before acting.")
    if str(candidate["action"]).startswith("BUY"):
        candidate["action"] = "WATCH"


def run_research_scan(
    ltp_by_symbol: dict[str, float] | None = None,
    quote_fetcher: Callable[[list[str]], dict[str, float]] | None = None,
    capital: float = 100_000,
    risk_pct: float = 1.0,
    min_rr: float = 2.0,
    universe: str = "nifty500",
    derivatives_fetcher: Callable[[list[str], dict[str, float]], dict[str, dict[str, Any]]] | None = None,
    fno_members: list[dict[str, str]] | None = None,
    fno_source: str | None = None,
    fno_warning: str | None = None,
) -> dict[str, Any]:
    """Build daily, weekly, and monthly watchlists from current real market data."""
    generated_at = datetime.now(INDIA_TZ).isoformat(timespec="seconds")
    try:
        fii_dii_snapshot = latest_fii_dii()
    except Exception as exc:
        logger.warning("Imported FII/DII feed unavailable: %s", exc)
        fii_dii_snapshot = {"status": "unavailable", "reason": "Could not read the local FII/DII report import."}
    ltp_by_symbol = {key.upper(): value for key, value in (ltp_by_symbol or {}).items()}
    selected_universe = str(universe or "nifty500").strip().lower().replace(" ", "")
    if selected_universe not in {"nifty50", "nifty500", "fno"}:
        raise ValueError("universe must be 'nifty50', 'nifty500', or 'fno'")
    custom_universe = (Path(__file__).resolve().parent / "watchlist.txt").exists()
    if selected_universe == "fno":
        metadata = list(fno_members or [])
        if not metadata:
            raise ValueError("The live Kotak NSE F&O stock universe is unavailable")
        universe_name = "NSE F&O stocks"
        universe_source = fno_source or "Kotak live NSE F&O instrument master"
        breadth_index = "NIFTY 500"
    else:
        universe_name = "Custom watchlist" if custom_universe else selected_universe.replace("nifty", "Nifty ") + " constituents"
        universe_source = constituents_source(selected_universe)
        breadth_index = "NIFTY 500" if selected_universe == "nifty500" else "NIFTY 50"
        metadata = load_universe_metadata(universe=selected_universe)
    symbols = list(dict.fromkeys(item["symbol"].upper() for item in metadata))
    requested_tickers = symbols + list(BENCHMARKS.values()) + list(GLOBAL_MARKETS.values())
    data = fetch_data_batch(requested_tickers, days=HISTORY_DAYS)
    stock_frames = {symbol: data[symbol] for symbol in symbols if symbol in data and not data[symbol].empty}
    benchmark_frames = {ticker: data[ticker] for ticker in requested_tickers if ticker in data}
    stock_metrics = {symbol: _technical_snapshot(frame) for symbol, frame in stock_frames.items()}
    stock_metrics = {symbol: metrics for symbol, metrics in stock_metrics.items() if metrics}
    metadata_by_symbol = {item["symbol"].upper(): item for item in metadata}
    if not stock_metrics:
        unavailable = "LIVE DATA NOT AVAILABLE — I WILL NOT FABRICATE THE SETUP."
        return {
            "generated_at": generated_at,
            "universe": {"name": universe_name, "requested_count": len(symbols), "data_fetched_count": 0, "source": universe_source, "as_of": generated_at},
            "market_regime": {"classification": "UNAVAILABLE", "score": None, "benchmark": breadth_index, "rationale": [unavailable], "indices": {}, "india_vix": {"status": "unavailable"}, "breadth": {}, "fii_dii": fii_dii_snapshot, "global_cues": {}},
            "sector_rotation": {"strongest": [], "weakest": [], "method": "Unavailable without real OHLCV data."},
            "short_swing": [], "weekly_swing": [], "monthly_swing": [], "stocks_to_avoid": [],
            "ai_pattern_signals": {"status": "unavailable", "model": MODEL_NAME, "note": "Real OHLCV history was unavailable; no historical pattern signal was generated.", "coverage": {}, "short_swing": [], "weekly_swing": [], "monthly_swing": []},
            "coverage": {"status": "unavailable", "message": unavailable, "news": "unavailable", "fundamentals": "unavailable", "derivatives": "unavailable", "fii_dii": "provider_required", "delivery": "provider_required", "corporate_events": "unavailable", "intraday": "unavailable"},
            "sources": [
                {"name": "NSE FII/FPI & DII report", "url": "https://www.nseindia.com/reports/fii-dii", "description": "Official daily aggregate cash-market flows. To ingest automatically, configure an authorized data subscription; public website scraping is disabled."},
                {"name": "Kotak Neo equity derivatives", "url": "https://github.com/Kotak-Neo/kotak-neo-python", "description": "The authenticated Kotak session supplies shortlisted stock futures and a near-ATM option OI snapshot when the current F&O master is available."},
                {"name": "NSE security-wise delivery positions", "url": "https://www.nseindia.com/all-reports", "description": "Official end-of-day deliverable quantities and percentages. Configure an authorized feed or import a report; not an intraday measure."},
                {"name": "NSE corporate filings", "url": "https://www.nseindia.com/companies-listing/corporate-filings-announcements", "description": "Official issuer-submitted announcements. Scanner headline/calendar flags are preliminary and must be verified against the filing."},
                {"name": "NSE market-data subscriptions", "url": "https://www.nseindia.com/static/market-data/real-time-data-subscription", "description": "NSE Data & Analytics offers real-time, snapshot, and delayed feeds by segment; subscription terms apply."},
            ],
            "market_opening_analysis": {"status": unavailable, "bullish_scenario": "Live bars unavailable; no bullish setup can be confirmed.", "bearish_scenario": "Live bars unavailable; no bearish setup can be confirmed.", "no_trade_scenario": unavailable},
            "warnings": [unavailable],
            "warning": unavailable,
            "no_trade": True,
            "no_trade_message": unavailable,
            "assumptions": {"capital_rupees": capital, "risk_pct": risk_pct, "minimum_risk_reward": min_rr},
        }

    market, benchmark_return_20d = _market_regime(benchmark_frames, stock_metrics, breadth_index=breadth_index)
    market["fii_dii"] = fii_dii_snapshot
    if fii_dii_snapshot.get("fii_net_cr") is not None or fii_dii_snapshot.get("dii_net_cr") is not None:
        fii_value = _round(fii_dii_snapshot.get("fii_net_cr"))
        dii_value = _round(fii_dii_snapshot.get("dii_net_cr"))
        market["rationale"].append(
            f"Imported cash-market flows for {fii_dii_snapshot.get('date')}: FII/FPI {fii_value if fii_value is not None else 'unavailable'} Cr; DII {dii_value if dii_value is not None else 'unavailable'} Cr (reported aggregate, not used as a direction score)."
        )
    selected_benchmark_frame = benchmark_frames.get(BENCHMARKS[breadth_index])
    selected_benchmark_metrics = _technical_snapshot(selected_benchmark_frame) if selected_benchmark_frame is not None else None
    for metrics in stock_metrics.values():
        metrics["relative_strength_20d_pct"] = (
            metrics.get("return_20d_pct") - benchmark_return_20d
            if metrics.get("return_20d_pct") is not None and benchmark_return_20d is not None
            else None
        )

    sectors = _sector_rotation(metadata, stock_metrics, benchmark_return_20d)
    sectors_by_name = {row["sector"]: row for row in sectors}
    regime = market["classification"]
    grouped: dict[str, list[dict[str, Any]]] = {"short": [], "weekly": [], "monthly": []}
    ai_predictions: dict[str, dict[str, dict[str, Any]]] = {"short": {}, "weekly": {}, "monthly": {}}
    ai_coverage: dict[str, dict[str, int]] = {}
    if selected_universe == "fno":
        ai_predictions, ai_coverage = score_universe_patterns(stock_frames)
    grouped_ai: dict[str, list[dict[str, Any]]] = {"short": [], "weekly": [], "monthly": []}
    for symbol, frame in stock_frames.items():
        metrics = stock_metrics.get(symbol)
        if not metrics:
            continue
        metadata_item = metadata_by_symbol.get(symbol, {"symbol": symbol, "name": symbol, "industry": "Unknown"})
        sector = sectors_by_name.get(str(metadata_item.get("industry") or "Unknown"))
        for timeframe in grouped:
            pattern = _candidate_pattern(timeframe, frame, metrics, min_rr)
            if pattern and pattern["rr"] >= min_rr:
                candidate = _make_candidate(
                    symbol, timeframe, metadata_item, frame, metrics, pattern, sector, regime,
                    ltp_by_symbol, capital, risk_pct,
                )
                if candidate:
                    grouped[timeframe].append(candidate)

            analog = ai_predictions.get(timeframe, {}).get(symbol)
            ai_pattern = _ai_candidate_pattern(timeframe, metrics, analog, min_rr) if analog else None
            if ai_pattern and ai_pattern["rr"] >= min_rr:
                ai_candidate = _make_candidate(
                    symbol, timeframe, metadata_item, frame, metrics, ai_pattern, sector, regime,
                    ltp_by_symbol, capital, risk_pct,
                )
                if ai_candidate:
                    grouped_ai[timeframe].append(ai_candidate)

    limits = {"short": 3, "weekly": 5, "monthly": 5}
    for candidates in grouped.values():
        candidates.sort(key=lambda item: (item["setup_score"], item["risk_reward"] or 0), reverse=True)
    selected = {timeframe: grouped[timeframe][:limit] for timeframe, limit in limits.items()}
    for candidates in grouped_ai.values():
        candidates.sort(key=lambda item: (item["ai_pattern"]["match_strength"], item["setup_score"]), reverse=True)
    selected_ai = {timeframe: grouped_ai[timeframe][:5] for timeframe in grouped_ai}
    all_selected = [
        candidate
        for selected_group in (selected, selected_ai)
        for timeframe in ("short", "weekly", "monthly")
        for candidate in selected_group[timeframe]
    ]
    selected_symbols = list(dict.fromkeys(
        candidate["symbol"] for candidate in all_selected
    ))
    try:
        delivery_by_symbol, delivery_coverage = latest_delivery(selected_symbols)
        imported_events_by_symbol = recent_corporate_events(selected_symbols)
    except Exception as exc:
        logger.warning("Imported scanner reports unavailable: %s", exc)
        delivery_by_symbol = {}
        delivery_coverage = {"status": "unavailable", "symbols_with_data": 0, "symbols_checked": len(selected_symbols), "reason": "Could not read the local delivery report import."}
        imported_events_by_symbol = {}
    quote_error = None
    if quote_fetcher and selected_symbols:
        try:
            fetched_quotes = quote_fetcher(selected_symbols)
            ltp_by_symbol.update({str(symbol).upper(): value for symbol, value in fetched_quotes.items()})
        except Exception as exc:
            quote_error = str(exc)[:200]
            logger.warning("Kotak scanner quotes unavailable: %s", quote_error)
    derivative_error = None
    derivative_snapshots: dict[str, dict[str, Any]] = {}
    if derivatives_fetcher and selected_symbols:
        try:
            derivative_snapshots = derivatives_fetcher(selected_symbols, ltp_by_symbol)
        except Exception as exc:
            derivative_error = str(exc)[:200]
            logger.warning("Kotak scanner derivatives unavailable: %s", derivative_error)
    for candidate in all_selected:
        _apply_candidate_quote(candidate, ltp_by_symbol.get(candidate["symbol"]), regime)
        candidate["derivatives"] = derivative_snapshots.get(candidate["symbol"], {
            "status": "unavailable",
            "reason": derivative_error or (
                "Kotak session is not connected; log in to request stock F&O quotes."
                if not derivatives_fetcher else "No live derivative snapshot was returned for this symbol."
            ),
        })
        delivery_snapshot = delivery_by_symbol.get(candidate["symbol"])
        if delivery_snapshot is None:
            delivery_status = delivery_coverage.get("status")
            if delivery_status == "provider_required":
                delivery_reason = delivery_coverage.get("reason") or "Upload an authorized delivery report."
            elif delivery_status == "stale":
                delivery_reason = delivery_coverage.get("reason") or "Latest imported delivery report is stale."
            else:
                delivery_reason = "Latest imported delivery report does not include this symbol."
            delivery_snapshot = {"status": delivery_status or "unavailable", "reason": delivery_reason}
        candidate["delivery"] = delivery_snapshot
    company_contexts = _fetch_company_contexts(selected_symbols)
    for candidate in all_selected:
        _apply_company_context(
            candidate,
            company_contexts.get(candidate["symbol"], {}),
            imported_events_by_symbol.get(candidate["symbol"], []),
        )
    for candidates in selected.values():
        candidates.sort(key=lambda item: (item["setup_score"], item["risk_reward"] or 0), reverse=True)
    for candidates in selected_ai.values():
        candidates.sort(key=lambda item: (item["ai_pattern"]["match_strength"], item["setup_score"]), reverse=True)

    short_swing = selected["short"]
    weekly_swing = selected["weekly"]
    monthly_swing = selected["monthly"]
    data_count = len(stock_frames)
    fundamental_count = sum(1 for context in company_contexts.values() if context.get("fundamental_metrics"))
    news_count = sum(1 for context in company_contexts.values() if context.get("news"))
    corporate_event_symbols = {
        candidate["symbol"] for candidate in all_selected
        if candidate.get("corporate_events", {}).get("status") in {"partial", "available"}
    }
    corporate_event_count = len(corporate_event_symbols)
    imported_event_count = len(imported_events_by_symbol)
    derivative_count = sum(1 for snapshot in derivative_snapshots.values() if snapshot.get("status") in {"available", "partial"})
    ai_signal_count = sum(len(candidates) for candidates in selected_ai.values())
    enough_ai_history = any(
        info.get("training_samples", 0) >= MIN_ANALOGS
        and info.get("training_symbols", 0) >= MIN_DISTINCT_SYMBOLS
        for info in ai_coverage.values()
    )
    ai_status = (
        "not_requested" if selected_universe != "fno"
        else "available" if ai_signal_count
        else "no_qualifying_patterns" if enough_ai_history
        else "insufficient_history"
    )
    ai_note = (
        f"{MODEL_NAME} compared completed {', '.join(f'{horizon}-session' for horizon in HORIZONS.values())} analog windows. "
        "The sample uses today's eligible F&O names, so it is not a survivorship-free backtest. "
        "Historical outcomes are descriptive evidence, not a forecast or win probability. Levels refer to the underlying shares; derivative premium, liquidity, and margin are not modeled."
    ) if selected_universe == "fno" else "Select the F&O universe to run the historical analog model."
    warnings = []
    if data_count < len(symbols):
        warnings.append(f"Real OHLCV data returned for {data_count} of {len(symbols)} symbols in the selected universe.")
    if selected_universe == "fno":
        warnings.append(f"F&O membership source: {universe_source}; only currently unexpired stock derivative rows are included.")
        if fno_warning:
            warnings.append(f"{fno_warning} Cached membership was used for symbols only; stale contract tokens were not used for derivative quotes.")
        warnings.append(f"AI pattern model returned {ai_signal_count} qualified historical analog signals. This experimental model is not a validated trading edge or probability.")
    quoted_symbol_count = sum(1 for symbol in selected_symbols if symbol in ltp_by_symbol)
    if selected_symbols and quoted_symbol_count == 0:
        warnings.append("Kotak live quotes were unavailable for the shortlisted symbols; those candidates are marked NO TRADE.")
    elif quoted_symbol_count < len(selected_symbols):
        warnings.append(f"Kotak live quotes were available for {quoted_symbol_count} of {len(selected_symbols)} shortlisted symbols; candidates without a quote are marked NO TRADE.")
    if quote_error:
        warnings.append(f"Kotak quote lookup failed: {quote_error}")
    if not selected_benchmark_metrics:
        warnings.append(f"{breadth_index} benchmark history is unavailable; market-regime scoring is incomplete.")
    warnings.append(f"Source-linked Yahoo Finance fundamentals/news returned for {fundamental_count}/{len(company_contexts)} and {news_count}/{len(company_contexts)} shortlisted symbols; {corporate_event_count}/{len(company_contexts)} have event context and {imported_event_count} have imported event records. Verify dates and disclosures at source.")
    if fii_dii_snapshot.get("status") in {"provider_required", "unavailable"}:
        warnings.append("FII/DII data is missing: import an authorized report CSV from the scanner. The NSE website is not automatically scraped.")
    elif fii_dii_snapshot.get("status") == "stale":
        warnings.append("Imported FII/DII data is more than seven days old; refresh the report before using it.")
    if delivery_coverage.get("status") in {"provider_required", "unavailable"}:
        warnings.append("Delivery data is missing: import an authorized NSE MTO delivery report or compatible CSV from the scanner.")
    elif delivery_coverage.get("status") == "stale":
        warnings.append("Imported security-wise delivery data is more than seven days old; refresh the report before using it.")
    if derivatives_fetcher and selected_symbols:
        warnings.append(f"Kotak futures/near-ATM option snapshots returned for {derivative_count}/{len(selected_symbols)} shortlisted symbols; option OI covers up to seven strikes near spot, not the full chain.")
    else:
        warnings.append("Log in to Kotak to request shortlisted stock-derivative data. FII/DII and delivery checks still need an authorized data feed; automated NSE website collection is disabled.")
    warnings.append("Intraday 5m/15m and 1h/4h bars are unavailable; opening-range and VWAP-reclaim checks are not performed.")

    return {
        "generated_at": generated_at,
        "universe": {
            "name": universe_name,
            "requested_count": len(symbols),
            "data_fetched_count": data_count,
            "source": universe_source,
            "as_of": generated_at,
        },
        "market_regime": market,
        "sector_rotation": {
            "strongest": sectors[:5],
            "weakest": list(reversed(sectors[-5:])),
            "method": f"Average 20/60-session returns of fetched {breadth_index} constituents by official constituent industry; not a full sector-index study.",
        },
        "short_swing": short_swing,
        "weekly_swing": weekly_swing,
        "monthly_swing": monthly_swing,
        "ai_pattern_signals": {
            "status": ai_status,
            "model": MODEL_NAME,
            "note": ai_note,
            "coverage": ai_coverage,
            "short_swing": selected_ai["short"],
            "weekly_swing": selected_ai["weekly"],
            "monthly_swing": selected_ai["monthly"],
        },
        "stocks_to_avoid": _avoid_flags(stock_metrics),
        "coverage": {
            "status": "partial",
            "news": {"status": "unavailable" if not company_contexts or news_count == 0 else "partial" if news_count < len(company_contexts) else "available", "symbols_with_recent_headlines": news_count, "symbols_checked": len(company_contexts)},
            "fundamentals": {"status": "unavailable" if not company_contexts or fundamental_count == 0 else "partial" if fundamental_count < len(company_contexts) else "available", "symbols_with_metrics": fundamental_count, "symbols_checked": len(company_contexts)},
            "derivatives": {"status": "not_requested" if not selected_symbols else "unavailable" if derivative_count == 0 else "partial" if derivative_count < len(selected_symbols) else "available", "provider": "Kotak Neo", "symbol_count": derivative_count, "requested_count": len(selected_symbols), "as_of": generated_at if derivative_count else None, "error": derivative_error},
            "fii_dii": fii_dii_snapshot,
            "delivery": delivery_coverage,
            "corporate_events": {"status": "unavailable" if not company_contexts or corporate_event_count == 0 else "available" if imported_events_by_symbol else "partial", "symbols_with_context": corporate_event_count, "symbols_checked": len(company_contexts), "provider": "Uploaded event CSV and Yahoo Finance aggregated calendar/headlines" if imported_events_by_symbol else "Yahoo Finance aggregated calendar/headlines"},
            "intraday": "unavailable",
            "daily_ohlcv": {"status": "available", "provider": "Yahoo Finance", "symbol_count": data_count, "bar_as_of": max((item["bar_as_of"] for item in stock_metrics.values()), default=None)},
            "live_quotes": {"status": "not_requested" if not selected_symbols else "unavailable" if quoted_symbol_count == 0 else "partial" if quoted_symbol_count < len(selected_symbols) else "available", "provider": "Kotak Neo", "symbol_count": quoted_symbol_count, "requested_count": len(selected_symbols), "as_of": generated_at if quoted_symbol_count else None},
            "ai_patterns": {"status": ai_status, "model": MODEL_NAME, "signal_count": ai_signal_count, "coverage": ai_coverage, "provider": "Local model over Yahoo Finance daily OHLCV; active F&O universe from Kotak Neo" if selected_universe == "fno" else None},
        },
        "sources": [
            {"name": "Current NSE F&O stock universe" if selected_universe == "fno" else f"{breadth_index} constituent list", "url": "https://github.com/Kotak-Neo/kotak-neo-python/blob/main/docs/functions/market_data/scrip_master.md" if selected_universe == "fno" else "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv" if selected_universe == "nifty500" else "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv", "description": universe_source},
            {"name": "Yahoo Finance market history", "url": "https://finance.yahoo.com/", "description": "Daily OHLCV and benchmark history; see each setup for its symbol history link."},
            {"name": "AI historical pattern model", "url": None, "description": f"{MODEL_NAME}; analogs use prior completed OHLCV states and forward returns, with a cap per source symbol. Historical match statistics are not probabilities or verified performance."},
            {"name": "Kotak Neo", "url": "https://github.com/Kotak-Neo/kotak-neo-python", "description": "Current quote snapshots are requested only for shortlisted symbols to stay within broker request limits."},
            {"name": "NSE FII/FPI & DII report", "url": "https://www.nseindia.com/reports/fii-dii", "description": "Import the manually downloaded authorized report CSV above the scan. Values are daily aggregate cash-market flows, not stock-specific activity."},
            {"name": "Kotak Neo equity derivatives", "url": "https://github.com/Kotak-Neo/kotak-neo-python", "description": "Authenticated broker snapshots for shortlisted stock futures and up to seven near-ATM option strikes; quote coverage depends on the current Kotak F&O master."},
            {"name": "NSE security-wise delivery positions", "url": "https://www.nseindia.com/all-reports", "description": "Import an authorized MTO_ddmmyyyy.DAT report or compatible CSV above the scan. These are end-of-day values."},
            {"name": "NSE corporate filings", "url": "https://www.nseindia.com/companies-listing/corporate-filings-announcements", "description": "Import a CSV with symbol, event, date, and optional url/source columns to add source-linked event checks. Yahoo headline/calendar flags are preliminary."},
            {"name": "NSE website terms", "url": "https://www.nseindia.com/static/nse-terms-of-use", "description": "NSE prohibits systematic or automated data collection from its public website."},
            {"name": "NSE market-data subscriptions", "url": "https://www.nseindia.com/static/market-data/real-time-data-subscription", "description": "NSE Data & Analytics offers real-time, snapshot, and delayed feeds by segment; subscription terms apply."},
        ],
        "warnings": warnings,
        "market_opening_analysis": {
            "status": "Intraday bars are not connected; these are rules, not today's confirmed open scenarios.",
            "bullish_scenario": "Consider a long only after a 15-minute close above the prior-day high, above VWAP, with expanding volume and NIFTY/sector alignment.",
            "bearish_scenario": "Avoid cash-equity shorts. For derivatives research, require a 15-minute close below the prior-day low, below VWAP, with expanding volume and market/sector weakness.",
            "no_trade_scenario": "Stand aside when index and sector direction conflict, price remains inside the prior-day range, volume is weak, or the live quote is unavailable.",
        },
        "warning": " ".join(warnings) if warnings else None,
        "no_trade": not (short_swing or weekly_swing or monthly_swing or ai_signal_count),
        "no_trade_message": "NO TRADE — no setups passed the technical and minimum risk/reward filters." if not (short_swing or weekly_swing or monthly_swing or ai_signal_count) else None,
        "assumptions": {"capital_rupees": capital, "risk_pct": risk_pct, "minimum_risk_reward": min_rr},
    }
