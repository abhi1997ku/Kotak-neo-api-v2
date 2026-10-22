"""Explainable nearest-neighbour pattern research over historical daily OHLCV.

This is a small, data-driven analog model rather than a generative model. Every
training label uses only returns that had completed before the scan date, and
the response exposes the analog sample count and historical outcomes.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


MODEL_NAME = "Daily OHLCV historical analogs (nearest neighbours)"
HORIZONS = {"short": 3, "weekly": 10, "monthly": 20}
MIN_ANALOGS = 24
MIN_DISTINCT_SYMBOLS = 8
NEIGHBOURS = 48
MAX_NEIGHBOURS_PER_SYMBOL = 4
FEATURE_COLUMNS = (
    "return_5d",
    "return_20d",
    "return_60d",
    "distance_sma20",
    "distance_sma50",
    "distance_sma200",
    "rsi14",
    "relative_volume_log",
    "range_position_60d",
    "atr_pct",
)


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    close = pd.to_numeric(frame["Close"], errors="coerce")
    high = pd.to_numeric(frame["High"], errors="coerce").reindex(close.index)
    low = pd.to_numeric(frame["Low"], errors="coerce").reindex(close.index)
    volume = pd.to_numeric(frame["Volume"], errors="coerce").reindex(close.index)

    change = close.diff()
    gains = change.clip(lower=0).rolling(14, min_periods=14).mean()
    losses = (-change.clip(upper=0)).rolling(14, min_periods=14).mean()
    relative_strength = gains / losses.replace(0, np.nan)
    rsi = 100 - (100 / (1 + relative_strength))
    rsi = rsi.mask((losses == 0) & (gains > 0), 100)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_pct = true_range.rolling(14, min_periods=14).mean() / close.replace(0, np.nan)

    high_60 = high.rolling(60, min_periods=60).max()
    low_60 = low.rolling(60, min_periods=60).min()
    range_size = (high_60 - low_60).replace(0, np.nan)
    prior_volume_average = volume.rolling(20, min_periods=20).mean().shift(1)
    relative_volume = volume / prior_volume_average.replace(0, np.nan)

    result = pd.DataFrame(index=close.index)
    result["return_5d"] = close.pct_change(5)
    result["return_20d"] = close.pct_change(20)
    result["return_60d"] = close.pct_change(60)
    for period in (20, 50, 200):
        average = close.rolling(period, min_periods=period).mean()
        result[f"distance_sma{period}"] = close / average - 1
    result["rsi14"] = rsi / 100
    result["relative_volume_log"] = np.log1p(relative_volume.clip(lower=0))
    result["range_position_60d"] = (close - low_60) / range_size
    result["atr_pct"] = atr_pct
    return result.loc[:, list(FEATURE_COLUMNS)].replace([np.inf, -np.inf], np.nan)


def _minimum_move(horizon: int) -> float:
    return {3: 0.003, 10: 0.006, 20: 0.01}.get(horizon, 0.005)


def score_historical_patterns(
    frames: dict[str, pd.DataFrame],
    horizon: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Compare each latest chart state with prior cross-sectional OHLCV states.

    A historical row is eligible only if its full forward horizon is present.
    At most four analogs per source symbol are retained to avoid one stock
    dominating the comparison set.
    """
    feature_by_symbol: dict[str, pd.DataFrame] = {}
    training_features: list[np.ndarray] = []
    training_returns: list[np.ndarray] = []
    training_symbols: list[np.ndarray] = []
    usable_frames = [frame.sort_index() for frame in frames.values() if frame is not None and len(frame) >= 220]
    common_calendar = sorted({date for frame in usable_frames for date in frame.index})
    if not common_calendar:
        return {}, {"training_samples": 0, "training_symbols": 0}
    # Stop training labels early enough that every analog's forward outcome was
    # already complete by the shared scan date. This avoids cross-symbol leakage
    # when a few histories have a newer last candle than the rest.
    scan_as_of = min(frame.index[-1] for frame in usable_frames)
    scan_calendar = [date for date in common_calendar if date <= scan_as_of]
    cutoff_position = max(0, len(scan_calendar) - horizon - 1)
    training_cutoff = scan_calendar[cutoff_position]

    for symbol, frame in frames.items():
        if frame is None or frame.empty or len(frame) < 220:
            continue
        ordered = frame.sort_index()
        close = pd.to_numeric(ordered["Close"], errors="coerce")
        feature_frame = _features(ordered)
        feature_by_symbol[symbol] = feature_frame
        forward_return = close.shift(-horizon) / close - 1
        valid = feature_frame.notna().all(axis=1) & forward_return.notna() & (feature_frame.index <= training_cutoff)
        if valid.any():
            training_features.append(feature_frame.loc[valid].to_numpy(dtype=float))
            training_returns.append(forward_return.loc[valid].to_numpy(dtype=float))
            training_symbols.append(np.full(int(valid.sum()), symbol, dtype=object))

    if not training_features:
        return {}, {"training_samples": 0, "training_symbols": 0}

    x_train = np.concatenate(training_features)
    y_train = np.concatenate(training_returns)
    symbols_train = np.concatenate(training_symbols)
    center = np.nanmedian(x_train, axis=0)
    scale = np.nanstd(x_train, axis=0)
    scale = np.where(np.isfinite(scale) & (scale > 1e-9), scale, 1.0)
    x_scaled = np.clip((x_train - center) / scale, -6, 6)

    signals: dict[str, dict[str, Any]] = {}
    minimum_move = _minimum_move(horizon)
    for symbol, feature_frame in feature_by_symbol.items():
        latest = feature_frame.iloc[-1]
        if latest.isna().any():
            continue
        query = np.clip((latest.to_numpy(dtype=float) - center) / scale, -6, 6)
        distances = np.sqrt(np.mean(np.square(x_scaled - query), axis=1))
        nearest_order = np.argpartition(distances, min(NEIGHBOURS * 12, len(distances) - 1))[:min(NEIGHBOURS * 12, len(distances))]
        nearest_order = nearest_order[np.argsort(distances[nearest_order])]

        chosen: list[int] = []
        used_per_symbol: dict[str, int] = {}
        for index in nearest_order:
            source_symbol = str(symbols_train[index])
            count = used_per_symbol.get(source_symbol, 0)
            if count >= MAX_NEIGHBOURS_PER_SYMBOL:
                continue
            chosen.append(int(index))
            used_per_symbol[source_symbol] = count + 1
            if len(chosen) >= NEIGHBOURS:
                break
        if len(chosen) < MIN_ANALOGS or len(used_per_symbol) < MIN_DISTINCT_SYMBOLS:
            continue

        chosen_indexes = np.asarray(chosen, dtype=int)
        analog_returns = y_train[chosen_indexes]
        analog_distances = distances[chosen_indexes]
        weights = 1 / np.maximum(analog_distances, 0.15)
        weights /= weights.sum()
        weighted_mean = float(np.sum(analog_returns * weights))
        weighted_positive_rate = float(np.sum((analog_returns > 0) * weights))
        median_return = float(np.median(analog_returns))

        direction = None
        if weighted_positive_rate >= 0.60 and weighted_mean >= minimum_move and median_return > 0:
            direction = "LONG"
        elif weighted_positive_rate <= 0.40 and weighted_mean <= -minimum_move and median_return < 0:
            direction = "SHORT"
        if direction is None:
            continue

        match_strength = int(round(min(100, 35 + abs(weighted_positive_rate - 0.5) * 100 + min(len(chosen), 40) * 0.5)))
        signals[symbol] = {
            "direction": direction,
            "horizon_sessions": horizon,
            "analog_samples": len(chosen),
            "distinct_symbols": len(used_per_symbol),
            "historical_positive_rate_pct": round(weighted_positive_rate * 100, 1),
            "average_forward_return_pct": round(weighted_mean * 100, 2),
            "median_forward_return_pct": round(median_return * 100, 2),
            "match_strength": match_strength,
            "model": MODEL_NAME,
            "source_note": "Outcomes belong to completed historical analogs; they are not a forecast or win probability.",
        }

    return signals, {
        "training_samples": int(len(y_train)),
        "training_symbols": int(len(set(symbols_train.tolist()))),
    }


def score_universe_patterns(
    frames: dict[str, pd.DataFrame],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, int]]]:
    """Return per-horizon analog signals and model coverage counts."""
    by_horizon: dict[str, dict[str, dict[str, Any]]] = {}
    coverage: dict[str, dict[str, int]] = {}
    for name, horizon in HORIZONS.items():
        by_horizon[name], coverage[name] = score_historical_patterns(frames, horizon)
    return by_horizon, coverage
