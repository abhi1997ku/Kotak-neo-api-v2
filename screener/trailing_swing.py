"""Hourly index swing plans and causal paper tracking with an open target.

Price-action rules, not an accuracy-qualified ML forecast. Broker orders are
never submitted. Stops derived at a candle close activate on the next bar.
"""
from __future__ import annotations

import copy
import numpy as np
import pandas as pd

from screener.index_pattern import clean_bars, session_gaps

SETTINGS = {
    "NIFTY": {"move": [200, 330], "risk": [60, 130], "accelerate": [100, 150]},
    "BANKNIFTY": {"move": [500, 600], "risk": [150, 260], "accelerate": [100, 150]},
}
COST_PER_SIDE = .0002


def features(raw, now=None):
    bars = clean_bars(raw, now)
    f = bars.loc[bars.index.tz_convert("Asia/Kolkata").hour != 15].copy()
    x = np.arange(12, dtype=float)-5.5
    f["slope"] = f.Close.rolling(12).apply(lambda y: np.dot(x, y)/np.dot(x, x), raw=True)
    f["width"] = (f.High-f.Low).rolling(12).median()
    f["upper"] = f.High.rolling(8).max()
    f["lower"] = f.Low.rolling(8).min()
    f["efficiency"] = f.Close.diff(12).abs()/f.Close.diff().abs().rolling(12).sum().replace(0, np.nan)
    f["normalized_slope"] = f.slope/f.width.replace(0, np.nan)
    midpoint = (f.upper+f.lower)/2
    f["direction"] = 0
    f.loc[(f.normalized_slope >= .08) & (f.efficiency >= .25) & (f.Close > midpoint), "direction"] = 1
    f.loc[(f.normalized_slope <= -.08) & (f.efficiency >= .25) & (f.Close < midpoint), "direction"] = -1
    f.iloc[:60, f.columns.get_loc("direction")] = 0
    # After an unexplained data gap, rebuild a complete context before entry.
    counts = np.r_[0, np.cumsum(session_gaps(bars))]
    positions = bars.index.get_indexer(f.index)
    for i, pos in enumerate(positions):
        if counts[pos+1]-counts[positions[max(0, i-60)]]:
            f.iloc[i, f.columns.get_loc("direction")] = 0
    return bars, f


def make_plan(symbol, row, timestamp, direction=None):
    direction = int(row.direction if direction is None else direction)
    if direction not in (-1, 1) or not np.isfinite(row.width):
        return None
    cfg = SETTINGS[symbol]
    risk = round(float(np.clip(row.width*1.8, *cfg["risk"])), 2)
    buffer = round(max(1., row.width*.05), 2)
    trigger = round(float(row.upper+buffer if direction == 1 else row.lower-buffer), 2)
    return {"id": f"{symbol}:{timestamp.isoformat()}:{direction}", "symbol": symbol,
            "direction": direction, "side": "LONG" if direction == 1 else "SHORT",
            "signal_at": timestamp.isoformat(), "valid_after": (timestamp+pd.Timedelta(hours=1)).isoformat(),
            "entry_trigger": trigger, "initial_stop": round(trigger-direction*risk, 2),
            "initial_risk_points": risk, "max_entry_gap_points": round(risk*.25, 2),
            "milestones": [round(trigger+direction*m, 2) for m in cfg["move"]],
            "trail_acceleration_points": cfg["accelerate"],
            "target": None, "hours_left": 3, "status": "ARMED",
            "reason": "12-hour regression slope / median range must be at least 0.08 in the trade direction, with path efficiency at least 0.25. Enter beyond the eight-hour range plus a 5%-of-range buffer."}


def open_trade(plan, entry, timestamp):
    trade = copy.deepcopy(plan)
    trade.update(entry=round(float(entry), 2), entry_at=timestamp.isoformat(),
                 stop=plan["initial_stop"], remaining=1., legs=[], events=[],
                 trail_stage="NORMAL", exit_pending=False,
                 best_price=round(float(entry), 2), bars_held=0, status="OPEN")
    trade["risk_points"] = round(abs(trade["entry"]-trade["stop"]), 2)
    trade["milestones"] = [round(trade["entry"]+trade["direction"]*m, 2) for m in SETTINGS[trade["symbol"]]["move"]]
    return trade


def book(trade, fraction, price, timestamp, kind):
    fraction = min(fraction, trade["remaining"])
    if fraction <= 0:
        return
    price = round(float(price), 2)
    trade["legs"].append({"at": timestamp.isoformat(), "kind": kind, "fraction": fraction,
                          "price": price, "gross_points": round(fraction*trade["direction"]*(price-trade["entry"]), 4),
                          "cost_points": round(fraction*price*COST_PER_SIDE, 4)})
    trade["remaining"] = round(trade["remaining"]-fraction, 4)
    if trade["remaining"] == 0:
        trade.update(status="CLOSED", exit_at=timestamp.isoformat(), exit_reason=kind)


def manage_bar(trade, bar, timestamp, full_row=None, *, new_entry=False):
    """Existing stop checks precede updates; new stops only affect later bars."""
    d = trade["direction"]
    stop = trade["stop"]
    trade["bars_held"] += 1
    if trade["exit_pending"]:
        book(trade, trade["remaining"], bar.Open, timestamp, "TRAIL_CLOSE_BREACH")
        return
    if not new_entry and d*(bar.Open-stop) <= 0:
        book(trade, trade["remaining"], bar.Open, timestamp, "STOP_GAP")
        return
    if (bar.Low <= stop if d == 1 else bar.High >= stop):
        book(trade, trade["remaining"], stop, timestamp, "TRAIL_STOP" if d*(stop-trade["initial_stop"]) > 0 else "INITIAL_STOP")
        return
    # The current completed candle's high cannot retrospectively tighten a stop
    # and then stop out at the same candle's low.
    trade["best_price"] = max(trade["best_price"], float(bar.High)) if d == 1 else min(trade["best_price"], float(bar.Low))
    if full_row is None:
        return
    cfg = SETTINGS[trade["symbol"]]
    favorable = d*(trade["best_price"]-trade["entry"])
    distance = trade["risk_points"]
    lock = -trade["risk_points"]
    trade["trail_stage"] = "NORMAL"
    if favorable >= cfg["accelerate"][1]:
        distance = min(distance, max(10., float(full_row.width)*.50))
        lock = favorable*.75
        trade["trail_stage"] = "FAST"
    elif favorable >= cfg["accelerate"][0]:
        distance = min(distance, max(15., float(full_row.width)*.75))
        lock = favorable*.50
        trade["trail_stage"] = "TIGHT"
    options = [stop, trade["entry"]+d*lock, trade["best_price"]-d*distance]
    proposed = max(options) if d == 1 else min(options)
    tightened = round(max(stop, proposed) if d == 1 else min(stop, proposed), 2)
    # If a reversal closed through the newly calculated stop, exit at the next
    # available open. Never pretend the new stop filled earlier in this bar.
    trade["exit_pending"] = bool(d*(bar.Close-tightened) <= 0)
    if tightened != stop:
        trade["stop"] = tightened
        trade["events"].append({"at": (timestamp+pd.Timedelta(hours=1)).isoformat(),
                                "kind": "STOP_TIGHTENED", "from": stop, "to": tightened,
                                "stage": trade["trail_stage"]})


def public_trade(trade, last_price=None):
    result = copy.deepcopy(trade)
    realized = sum(leg["gross_points"]-leg["cost_points"] for leg in trade["legs"])-trade["entry"]*COST_PER_SIDE
    result["realized_net_points"] = round(realized, 2)
    result["net_r"] = round(realized/trade["risk_points"], 3)
    result["locked_points_per_remaining_unit"] = round(trade["direction"]*(trade["stop"]-trade["entry"]), 2)
    result["favorable_move_points"] = round(trade["direction"]*(trade["best_price"]-trade["entry"]), 2)
    result["open_points"] = round(trade["remaining"]*trade["direction"]*(last_price-trade["entry"]), 2) if last_price is not None else None
    result["events"] = result["events"][-40:]
    return result


def replay(raw, symbol, now=None):
    bars, f = features(raw, now)
    if len(f) < 80:
        raise ValueError("At least 80 completed hourly candles required")
    active = pending = None
    closed, cancelled = [], []
    cooldown = 0
    gaps = session_gaps(bars)
    for i, (timestamp, bar) in enumerate(bars.iterrows()):
        row = f.loc[timestamp] if timestamp in f.index else None
        if gaps[i]:
            if active:
                # Missing candles prevent a reliable trade outcome; never count
                # an unknown stop/exit as a winning or losing backtest trade.
                cancelled.append({"id": active["id"], "reason": "DATA_GAP", "at": timestamp.isoformat()})
            active = pending = None
        if active:
            manage_bar(active, bar, timestamp, row)
            if active["status"] == "CLOSED":
                closed.append(public_trade(active))
                active = None
                cooldown = 2
        elif pending:
            d, trigger = pending["direction"], pending["entry_trigger"]
            touched = bar.High >= trigger if d == 1 else bar.Low <= trigger
            if touched:
                entry = max(float(bar.Open), trigger) if d == 1 else min(float(bar.Open), trigger)
                if d*(entry-trigger) > pending["max_entry_gap_points"]:
                    cancelled.append({"id": pending["id"], "reason": "ENTRY_GAP_TOO_LARGE", "at": timestamp.isoformat()})
                    pending = None
                else:
                    active = open_trade(pending, entry, timestamp)
                    pending = None
                    manage_bar(active, bar, timestamp, row, new_entry=True)
                    if active["status"] == "CLOSED":
                        closed.append(public_trade(active))
                        active = None
                        cooldown = 2
            if pending and row is not None:
                pending["hours_left"] -= 1
                if pending["hours_left"] <= 0 or int(row.direction) != pending["direction"]:
                    pending = None
        if row is not None and not active and not pending:
            if cooldown:
                cooldown -= 1
            else:
                pending = make_plan(symbol, row, timestamp)
    return bars, f, active, pending, closed, cancelled


def analyze(raw, symbol, now=None):
    if symbol not in SETTINGS:
        raise ValueError("Only NIFTY and BANKNIFTY are supported")
    bars, f, active, pending, closed, cancelled = replay(raw, symbol, now)
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    local = current.tz_convert("Asia/Kolkata")
    expected = local.normalize()
    if local.hour < 10 or (local.hour == 10 and local.minute < 15):
        expected -= pd.Timedelta(days=1)
    while expected.dayofweek >= 5:
        expected -= pd.Timedelta(days=1)
    stale = bars.index[-1].tz_convert("Asia/Kolkata").date() < expected.date()
    if local.dayofweek < 5 and 10*60+15 <= local.hour*60+local.minute <= 15*60+30:
        stale = stale or current-bars.index[-1] > pd.Timedelta(hours=2)
    market_open = local.dayofweek < 5 and 555 <= local.hour*60+local.minute < 930
    mode = "MODEL_POSITION" if active else "ENTRY_ARMED" if pending else "WAITING_FOR_TREND"
    profits = [t["realized_net_points"] for t in closed]
    rs = [t["net_r"] for t in closed]
    equity = np.cumsum([0., *rs])
    losses = -sum(r for r in rs if r < 0)
    return {"symbol": symbol, "strategy": "Hourly mathematical breakout + accelerating trailing stop", "status": mode,
            "data_status": "STALE" if stale else "CURRENT_SESSION" if market_open else "LAST_SESSION",
            "actionable": not stale and market_open and pending is not None,
            "as_of": bars.index[-1].isoformat(), "last_price": round(float(bars.iloc[-1].Close), 2),
            "desired_move_points": SETTINGS[symbol]["move"], "target": None,
            "trail_acceleration_points": SETTINGS[symbol]["accelerate"],
            "trade": public_trade(active, float(bars.iloc[-1].Close)) if active else None,
            "setup": pending,
            "watch_levels": {side: make_plan(symbol, f.iloc[-1], f.index[-1], d) for side, d in (("long", 1), ("short", -1))},
            "reason": ("Data is old: levels are historical, not a current entry instruction. " if stale else "")+
                      ("Track the model position's remaining quantity and trailing stop. The historical entry is not a fresh buy/sell instruction." if active else
                       "A conditional entry is armed. Wait for the trigger; target remains open." if pending else
                       "Trend filters or the post-exit cooldown are not aligned. Watch levels below are conditional references, not active orders."),
            "trailing_rules": ["Initial stop uses recent hourly range, capped by the index risk budget.",
                               "From the first favorable move, trail the best price by the initial risk distance.",
                               f"After +{SETTINGS[symbol]['accelerate'][0]} points, shorten the distance to 0.75 times median range (15-point floor) and protect at least 50% of peak movement.",
                               f"After +{SETTINGS[symbol]['accelerate'][1]} points, shorten it to 0.50 times median range (10-point floor) and protect at least 75% of peak movement.",
                               "Stops only tighten. A new hourly stop becomes effective on the following bar; a gap can fill worse than the stop.",
                               "If the completed close has already crossed the new stop, exit at the next bar open.",
                               "Hold the full position until the trailing stop exits it. No partial booking, fixed target, or time exit."],
            "backtest": {"closed_trades": len(closed), "profitable_trades": sum(p > 0 for p in profits),
                         "profitable_rate": round(100*sum(p > 0 for p in profits)/len(profits), 2) if profits else None,
                         "net_weighted_points": round(sum(profits), 2), "net_r": round(sum(rs), 3),
                         "profit_factor_r": round(sum(r for r in rs if r > 0)/losses, 3) if losses else None,
                         "max_closed_trade_drawdown_r": round(float(np.max(np.maximum.accumulate(equity)-equity)), 3),
                         "open_trades": int(active is not None), "unknown_data_gap_trades": sum(t["reason"] == "DATA_GAP" for t in cancelled),
                         "cost_bps_round_trip": 4, "recent_trades": closed[-20:],
                         "method": "Historical paper replay, not independent validation. Win means the full-position stop exit is net profitable after assumed costs. Newly computed stops apply on the following bar."}}
