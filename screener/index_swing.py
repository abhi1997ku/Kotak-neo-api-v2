"""Fixed-rule hourly index research agent. No execution or accuracy claims."""
import pandas as pd


def prepare(data, now=None):
    data = data.copy().sort_index()
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise ValueError("Hourly data must have timezone-aware timestamps")
    data = data.loc[~data.index.duplicated(keep="last")]
    now = pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    local = data.index.tz_convert("Asia/Kolkata")
    # NSE's final 15:15 candle is only 15 minutes, not a full hourly bar.
    minutes = local.hour * 60 + local.minute
    data = data.loc[(minutes >= 555) & (minutes <= 855) & (local.minute == 15)
                    & (local.dayofweek < 5) & (data.index + pd.Timedelta(hours=1) <= now)]
    cols = ["Open", "High", "Low", "Close"]
    if data.empty or data[cols].isna().any().any() or not data[cols].map(lambda x: float('-inf') < x < float('inf')).all().all():
        raise ValueError("Missing or invalid hourly prices")
    if ((data[cols] <= 0).any(axis=1) | (data.High < data[cols].max(axis=1)) | (data.Low > data[cols].min(axis=1))).any():
        raise ValueError("Invalid OHLC bars")
    close = data.Close
    data["fast"] = close.ewm(span=50, adjust=False).mean()
    data["slow"] = close.ewm(span=200, adjust=False).mean()
    tr = pd.concat([data.High-data.Low, (data.High-close.shift()).abs(), (data.Low-close.shift()).abs()], axis=1).max(axis=1)
    data["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    data["rsi"] = 100 * gain / (gain + loss).replace(0, float("nan"))
    data["upper"] = data.High.shift().rolling(20).max()
    data["lower"] = data.Low.shift().rolling(20).min()
    data["direction"] = 0
    data.loc[(close > data.upper) & (data.fast > data.slow) & (close > data.fast) & data.rsi.between(55, 70), "direction"] = 1
    data.loc[(close < data.lower) & (data.fast < data.slow) & (close < data.fast) & data.rsi.between(30, 45), "direction"] = -1
    data.iloc[:200, data.columns.get_loc("direction")] = 0
    return data


def levels(entry, atr, direction):
    risk = round(float(atr) * 1.5, 2)
    entry = round(float(entry), 2)
    return {"entry": entry, "stop": round(entry-direction*risk, 2),
            "target": round(entry+direction*3*risk, 2), "risk_points": risk}


def backtest(data):
    """Next-bar open entries, one position, adverse gaps, stop-first ambiguity."""
    trades = []
    position = None
    for i in range(201, len(data)):
        bar, previous = data.iloc[i], data.iloc[i-1]
        if position is None and previous.direction and previous.atr > 0:
            direction = int(previous.direction)
            position = dict(levels(bar.Open, previous.atr, direction), direction=direction)
        if position is None:
            continue
        p = position
        long = p["direction"] == 1
        stopped = bar.Low <= p["stop"] if long else bar.High >= p["stop"]
        target = bar.High >= p["target"] if long else bar.Low <= p["target"]
        if stopped or target:
            exit_price = (min(bar.Open, p["stop"]) if long else max(bar.Open, p["stop"])) if stopped else p["target"]
            trades.append(float((exit_price-p["entry"])*p["direction"]/p["risk_points"]))
            position = None
    wins = sum(r > 0 for r in trades)
    return {"closed_trades": len(trades), "wins": wins,
            "win_rate": round(100*wins/len(trades), 2) if trades else None,
            "total_r": round(sum(trades), 2), "open_trades": int(position is not None),
            "accuracy_verified": False,
            "method": "Fixed rules; next hourly open; stop first when both touched; adverse stop gaps; excludes costs. Historical results do not verify future accuracy."}


def analyze(data, now=None):
    raw = data.copy()
    data = prepare(data, now)
    if len(data) < 250:
        raise ValueError("At least 250 completed hourly bars required")
    bar = data.iloc[-1]
    current = pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    # Keep the closing 15-minute segment for fills/exits, but never indicators.
    local = raw.index.tz_convert("Asia/Kolkata")
    closing = raw.loc[(local.hour == 15) & (local.minute == 15)
                      & (local.dayofweek < 5)
                      & (raw.index + pd.Timedelta(minutes=15) <= current)]
    closing = closing.loc[~closing.index.duplicated(keep="last")]
    if not closing.empty:
        prices = closing[["Open", "High", "Low", "Close"]]
        if (not prices.map(lambda x: 0 < x < float("inf")).all().all()
                or (closing.High < prices.max(axis=1)).any()
                or (closing.Low > prices.min(axis=1)).any()):
            raise ValueError("Invalid closing session bars")
    execution = pd.concat([data, closing]).sort_index()
    execution["direction"] = execution.direction.fillna(0)
    execution["atr"] = execution.atr.ffill()
    stale = current - data.index[-1] - pd.Timedelta(hours=1) > pd.Timedelta(hours=2)
    direction = int(bar.direction)
    return {"status": "STALE" if stale else "LONG" if direction == 1 else "SHORT" if direction == -1 else "WAIT",
            "as_of": data.index[-1].isoformat(), "bars": len(data),
            "setup": levels(bar.Close, bar.atr, direction) if direction and not stale else None,
            "backtest": backtest(execution),
            "reason": "EMA 50/200 trend + 20-bar breakout + RSI 55–70 long / 30–45 short; stop 1.5 ATR(14). Entry is a reference at the completed close; recalculate levels at actual fill."}
