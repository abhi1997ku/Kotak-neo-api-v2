"""Hourly entries, lower-timeframe EMA trailing. No tick-feed claims or orders."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

from screener.trailing_swing import SETTINGS, book, features, make_plan, open_trade, public_trade
from screener.support_resistance import add_levels

EMA_SPAN = 21
VOLATILITY_SPAN = 14
WARMUP_BARS = 30


def minute_bars(raw, now=None):
    if raw is None or not isinstance(raw.index, pd.DatetimeIndex) or raw.index.tz is None:
        raise ValueError("Timezone-aware minute OHLC candles are required")
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    data = raw.copy().sort_index()
    data.index = data.index.tz_convert("UTC")
    data = data.loc[~data.index.duplicated(keep="last")]
    local = data.index.tz_convert("Asia/Kolkata")
    minute = local.hour*60+local.minute
    data = data.loc[(local.dayofweek < 5) & (minute >= 555) & (minute < 930)
                    & (local.second == 0) & (data.index+pd.Timedelta(minutes=1) <= current)]
    columns = ["Open", "High", "Low", "Close"]
    if data.empty or not all(c in data for c in columns):
        raise ValueError("Completed one-minute candles are unavailable")
    prices = data[columns].to_numpy(float)
    if (not np.isfinite(prices).all() or (prices <= 0).any()
            or (data.High < data[columns].max(axis=1)).any()
            or (data.Low > data[columns].min(axis=1)).any()):
        raise ValueError("Invalid minute OHLC prices")
    return data[columns]


def execution_features(raw, interval=1, now=None):
    if interval not in (1, 5):
        raise ValueError("Trailing interval must be 1 or 5 minutes")
    bars = minute_bars(raw, now)
    if interval == 5:
        grouped = bars.resample("5min", closed="left", label="left")
        count = grouped.Close.count()
        bars = grouped.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        bars = bars.loc[count == 5]  # Never invent absent minutes or use partial candles.
    if len(bars) < WARMUP_BARS:
        raise ValueError(f"Need at least {WARMUP_BARS} complete {interval}-minute candles")
    local = bars.index.tz_convert("Asia/Kolkata")
    gaps = np.zeros(len(bars), dtype=bool)
    for i in range(1, len(bars)):
        before, after = local[i-1], local[i]
        if before.date() == after.date():
            gaps[i] = after-before != pd.Timedelta(minutes=interval)
        else:
            gaps[i] = (before.hour*60+before.minute != 930-interval
                       or after.hour*60+after.minute != 555)
    bars["gap"] = gaps
    group = pd.Series(np.cumsum(gaps), index=bars.index)
    previous = bars.Close.groupby(group).shift()
    true_range = pd.concat([bars.High-bars.Low, (bars.High-previous).abs(), (bars.Low-previous).abs()], axis=1).max(axis=1)
    bars["ema"] = bars.Close.groupby(group).transform(lambda s: s.ewm(span=EMA_SPAN, adjust=False).mean())
    bars["volatility"] = true_range.groupby(group).transform(lambda s: s.ewm(span=VOLATILITY_SPAN, adjust=False).mean())
    bars["ready"] = bars.groupby(group).cumcount()+1 >= WARMUP_BARS
    return bars


def ema_stop(trade, ema, volatility):
    """Smooth buffer, with a monotone protective stop rather than fixed stages."""
    d = trade["direction"]
    profit_r = max(0., d*(trade["best_price"]-trade["entry"])/trade["risk_points"])
    multiplier = 1.+1.5*math.exp(-profit_r)
    buffer = float(volatility)*multiplier
    candidate = float(ema)-d*buffer
    stop = max(trade["stop"], candidate) if d == 1 else min(trade["stop"], candidate)
    return round(stop, 2), buffer, multiplier


def manage_minute(trade, bar, timestamp, interval, *, new_entry=False):
    d, old_stop = trade["direction"], trade["stop"]
    trade["bars_held"] += 1
    if trade["exit_pending"]:
        book(trade, trade["remaining"], bar.Open, timestamp, "EMA_CLOSE_BREACH")
        return
    if not new_entry and d*(bar.Open-old_stop) <= 0:
        book(trade, trade["remaining"], bar.Open, timestamp, "STOP_GAP")
        return
    if (bar.Low <= old_stop if d == 1 else bar.High >= old_stop):
        book(trade, trade["remaining"], old_stop, timestamp,
             "EMA_TRAIL_STOP" if d*(old_stop-trade["initial_stop"]) > 0 else "INITIAL_STOP")
        return
    trade["best_price"] = max(trade["best_price"], float(bar.High)) if d == 1 else min(trade["best_price"], float(bar.Low))
    stop, buffer, multiplier = ema_stop(trade, bar.ema, bar.volatility)
    effective_at = timestamp+pd.Timedelta(minutes=interval)
    trade.update(stop=stop, trail_stage="EMA", ema_price=round(float(bar.ema), 2),
                 volatility_buffer_points=round(buffer, 2), buffer_multiplier=round(multiplier, 4),
                 last_trail_update=effective_at.isoformat(), trailing_interval_minutes=interval,
                 exit_pending=bool(d*(bar.Close-stop) <= 0))
    if stop != old_stop:
        trade["events"].append({"at": effective_at.isoformat(), "kind": "EMA_STOP_UPDATE", "from": old_stop, "to": stop})


def _plan(symbol, row, timestamp, interval, direction=None):
    plan = make_plan(symbol, row, timestamp, direction)
    if plan:
        plan.pop("trail_acceleration_points", None)
        plan["id"] += f":ema{interval}"
        plan["trailing_interval_minutes"] = interval
        plan["entry_features"] = {key: float(row[key]) for key in
                                  ("Open", "High", "Low", "Close", "width", "upper", "lower",
                                   "slope", "normalized_slope", "efficiency", "support", "resistance",
                                   "previous_session_high", "previous_session_low")
                                  if key in row and np.isfinite(row[key])}
        plan["entry_features_available_at"] = plan["valid_after"]
    return plan


def replay(hourly, minutes, symbol, interval=1, now=None, records=None):
    full_bars, hourly_features = features(hourly, now)
    hourly_features = add_levels(hourly_features, full_bars)
    if len(hourly_features) < 80:
        raise ValueError("At least 80 hourly candles are needed for entry context")
    bars = execution_features(minutes, interval, now)
    available_at = hourly_features.index+pd.Timedelta(hours=1)
    # Start flat at the beginning of the real minute-data window. Do not invent
    # fills for hourly setups that occurred before minute data was available.
    cursor = int(available_at.searchsorted(bars.index[0], side="right"))
    active = pending = None
    closed, unknown = [], []
    cooldown = 0
    for timestamp, bar in bars.iterrows():
        if bar.gap:
            if active:
                unknown.append(active["id"])
                if records is not None:
                    record = public_trade(active)
                    record.update(events=active['events'].copy(), status="UNKNOWN_DATA_GAP",
                                  invalidated_at=timestamp.isoformat())
                    records.append(record)
            active = pending = None
        while cursor < len(hourly_features) and available_at[cursor] <= timestamp:
            signal = hourly_features.iloc[cursor]
            signal_time = hourly_features.index[cursor]
            cursor += 1
            if active:
                continue
            if pending:
                pending["hours_left"] -= 1
                if pending["hours_left"] <= 0 or signal.direction != pending["direction"]:
                    pending = None
            if cooldown:
                cooldown -= 1
                continue
            # Only arm at a real hourly boundary covered by this minute feed.
            if (pending is None and bar.ready and available_at[cursor-1] == timestamp):
                pending = _plan(symbol, signal, signal_time, interval)
        if active:
            manage_minute(active, bar, timestamp, interval)
        elif pending and bar.ready:
            d, trigger = pending["direction"], pending["entry_trigger"]
            if (bar.High >= trigger if d == 1 else bar.Low <= trigger):
                entry = max(float(bar.Open), trigger) if d == 1 else min(float(bar.Open), trigger)
                if d*(entry-trigger) <= pending["max_entry_gap_points"]:
                    active = open_trade(pending, entry, timestamp)
                    active.update(trail_stage="EMA", trailing_interval_minutes=interval)
                    manage_minute(active, bar, timestamp, interval, new_entry=True)
                pending = None
        if active and active["status"] == "CLOSED":
            if records is not None:
                record = public_trade(active)
                record['events'] = active['events'].copy()
                records.append(record)
            closed.append(public_trade(active))
            active = None
            cooldown = 2
    # A completed hour at the final lower-timeframe close can arm an entry for
    # the NEXT candle, but cannot use any price from a candle already replayed.
    final_close = bars.index[-1]+pd.Timedelta(minutes=interval)
    while cursor < len(hourly_features) and available_at[cursor] <= final_close:
        signal_time, signal = hourly_features.index[cursor], hourly_features.iloc[cursor]
        cursor += 1
        if active:
            continue
        if pending:
            pending["hours_left"] -= 1
            if pending["hours_left"] <= 0 or signal.direction != pending["direction"]:
                pending = None
        if cooldown:
            cooldown -= 1
        elif pending is None and bars.iloc[-1].ready and available_at[cursor-1] == final_close:
            pending = _plan(symbol, signal, signal_time, interval)
    if active and records is not None:
        record = public_trade(active, float(bars.iloc[-1].Close))
        record['events'] = active['events'].copy()
        records.append(record)
    return bars, hourly_features, active, pending, closed, unknown


def analyze(hourly, minutes, symbol, interval=1, now=None, journal=None):
    if symbol not in SETTINGS:
        raise ValueError("Unsupported index")
    records = [] if journal else None
    bars, hourly_features, active, pending, closed, unknown = replay(hourly, minutes, symbol, interval, now, records)
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    local = current.tz_convert("Asia/Kolkata")
    market_open = local.dayofweek < 5 and 555 <= local.hour*60+local.minute < 930
    expected = local.normalize()
    if local.hour*60+local.minute < 556:
        expected -= pd.Timedelta(days=1)
    while expected.dayofweek >= 5:
        expected -= pd.Timedelta(days=1)
    stale = bars.index[-1].tz_convert("Asia/Kolkata").date() < expected.date()
    if market_open:
        stale = stale or current-(bars.index[-1]+pd.Timedelta(minutes=interval)) > pd.Timedelta(minutes=3)
    # A minute candle must never use an hourly feature whose close is in its future.
    available = hourly_features.loc[hourly_features.index+pd.Timedelta(hours=1) <= bars.index[-1]+pd.Timedelta(minutes=interval)]
    if available.empty:
        raise ValueError("Hourly entry context does not overlap minute history")
    last_hour = available.iloc[-1]
    hour_time = available.index[-1]
    rs = [t["net_r"] for t in closed]
    net_points = [t["realized_net_points"] for t in closed]
    equity = np.cumsum([0., *rs])
    journal_status = journal(records, as_of=bars.index[-1].isoformat(),
                             period_start=bars.index[0].isoformat()) if journal else None
    return {"symbol": symbol, "strategy": f"1-hour breakout + {interval}-minute EMA{EMA_SPAN} trailing",
            "journal": journal_status,
            "support_resistance": {key: round(float(last_hour[key]), 2) if key in last_hour and np.isfinite(last_hour[key]) else None
                                   for key in ('support', 'resistance', 'previous_session_high', 'previous_session_low')},
            "status": "MODEL_POSITION" if active else "ENTRY_ARMED" if pending else "WAITING_FOR_TREND",
            "data_status": "STALE" if stale else "CURRENT_SESSION" if market_open else "LAST_SESSION",
            "actionable": bool(not stale and market_open and pending),
            "as_of": bars.index[-1].isoformat(), "hourly_signal_as_of": hour_time.isoformat(),
            "last_price": round(float(bars.iloc[-1].Close), 2), "target": None,
            "desired_move_points": SETTINGS[symbol]["move"], "trailing_interval_minutes": interval,
            "trailing_ema_period": EMA_SPAN, "latest_ema": round(float(bars.iloc[-1].ema), 2),
            "trade": public_trade(active, float(bars.iloc[-1].Close)) if active else None,
            "setup": pending,
            "watch_levels": {side: _plan(symbol, last_hour, hour_time, interval, d) for side, d in (("long", 1), ("short", -1))},
            "reason": ("Minute data is stale: these are historical levels. " if stale else "")+
                      ("Track the EMA stop on the existing model position; the historical entry is not a fresh order." if active else
                       "Hourly setup armed; monitor its breakout trigger on the lower-timeframe feed." if pending else
                       "Waiting for an aligned hourly setup after warmup/cooldown. Watch levels are conditional references."),
            "trailing_rules": ["Entries use completed 1-hour breakout signals; the trailing stop uses genuine lower-timeframe candles.",
                               f"EMA{EMA_SPAN} updates after every completed {interval}-minute candle. Volatility uses an exponentially smoothed 14-candle true range.",
                               "Buffer = volatility × (1 + 1.5 × exp(-peak profit / initial risk)). It narrows smoothly as profit grows, without 100/150-point stages.",
                               "Long stop = max(previous stop, EMA - buffer); short stop = min(previous stop, EMA + buffer). The protective stop never retreats.",
                               "Existing stops are checked throughout each candle's range before calculating the next stop. A close through the new stop exits at the next open.",
                               "Hold the full position until stopped; no partial profit-taking or fixed target. Data is polled, not a live broker tick stream."],
            "backtest": {"closed_trades": len(closed), "profitable_rate": round(100*sum(p > 0 for p in net_points)/len(closed), 2) if closed else None,
                         "net_weighted_points": round(sum(net_points), 2), "net_r": round(sum(rs), 3),
                         "max_closed_trade_drawdown_r": round(float(np.max(np.maximum.accumulate(equity)-equity)), 3),
                         "unknown_data_gap_trades": len(unknown), "recent_trades": closed[-20:],
                         "period_start": bars.index[0].isoformat(), "period_end": bars.index[-1].isoformat(),
                         "execution_bars": len(bars), "open_trades": int(active is not None),
                         "method": f"Actual {interval}-minute candle replay from the saved data window, starting flat; earlier trades are unknown. Missing-minute outcomes are excluded. Not an independent validation."}}
