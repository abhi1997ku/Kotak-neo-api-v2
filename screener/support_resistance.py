"""Causal hourly price-level context; does not change orders or stops."""
import numpy as np
import pandas as pd


def add_levels(hourly, full_bars):
    result = hourly.copy()
    local_dates = full_bars.index.tz_convert('Asia/Kolkata').date
    sessions = full_bars.groupby(local_dates).agg({'High': 'max', 'Low': 'min'})
    # Map only the preceding observed session: today's evolving extrema cannot leak.
    previous = sessions.shift(1)
    lows, highs = [], []
    output = []
    for i, (timestamp, row) in enumerate(hourly.iterrows()):
        # A five-bar pivot becomes knowable only after its two right-hand bars close.
        if i >= 4:
            window = hourly.iloc[i-4:i+1]
            center = window.iloc[2]
            # Require neighboring session context not to span an unexplained
            # intraday gap. Overnight boundaries are allowed.
            stamps = window.index.tz_convert('Asia/Kolkata')
            continuous = all(a.date() != b.date() or b-a == pd.Timedelta(hours=1)
                             for a, b in zip(stamps[:-1], stamps[1:]))
            if continuous:
                if center.Low < window.Low.drop(window.index[2]).min():
                    lows.append((i, float(center.Low)))
                if center.High > window.High.drop(window.index[2]).max():
                    highs.append((i, float(center.High)))
        lows = [(at, value) for at, value in lows if i-at < 60 and value < row.Close]
        highs = [(at, value) for at, value in highs if i-at < 60 and value > row.Close]
        day = timestamp.tz_convert('Asia/Kolkata').date()
        prior = previous.loc[day] if day in previous.index else None
        prev_high = float(prior.High) if prior is not None else np.nan
        prev_low = float(prior.Low) if prior is not None else np.nan
        supports = [v for _, v in lows]+([prev_low] if prev_low < row.Close else [])
        resistances = [v for _, v in highs]+([prev_high] if prev_high > row.Close else [])
        output.append((max(supports, default=np.nan), min(resistances, default=np.nan), prev_high, prev_low))
    result[['support', 'resistance', 'previous_session_high', 'previous_session_low']] = output
    return result
