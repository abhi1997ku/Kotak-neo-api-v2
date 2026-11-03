"""Causal stock shortlist research. No broker or order transport."""
from dataclasses import replace
import pandas as pd
from screener.order_block import scan, validate, OrderBlockConfig

TZ = "Asia/Kolkata"


def rank(frames, benchmark, at):
    """Use complete minute coverage and prior-session same-clock volume only."""
    at = pd.Timestamp(at).tz_convert(TZ)
    start = at.normalize() + pd.Timedelta(hours=9, minutes=15)
    expected = pd.date_range(start, at - pd.Timedelta(minutes=1), freq="min")
    if len(expected) < 15:
        return [], {"market": "Opening observation"}
    rejected, rows = {}, []
    benchmark = benchmark[benchmark.index + pd.Timedelta(minutes=1) <= at]
    validate(benchmark)
    bm = benchmark.reindex(expected)
    if bm.Close.isna().any():
        return [], {"market": "Missing benchmark minutes"}
    market_return = bm.Close.iloc[-1] / bm.Open.iloc[0] - 1
    for symbol, frame in frames.items():
        try:
            frame = frame[frame.index + pd.Timedelta(minutes=1) <= at]
            validate(frame)
            today = frame.reindex(expected)
            if today.isna().any().any() or (today.Volume <= 0).any():
                raise ValueError("Missing or zero-volume minutes")
            history = frame[frame.index.date < at.date()]
            volumes = []
            for _, session in history.groupby(history.index.date):
                clock = session.between_time("09:15", (at-pd.Timedelta(minutes=1)).strftime("%H:%M"))
                if len(clock) == len(expected) and (clock.index.to_series().diff().dropna() == pd.Timedelta(minutes=1)).all():
                    volumes.append(float(clock.Volume.sum()))
            if len(volumes) < 20:
                raise ValueError("Need 20 complete prior same-time sessions")
            baseline = float(pd.Series(volumes[-20:]).median())
            if baseline <= 0:
                raise ValueError("Invalid historical volume baseline")
            relative_volume = float(today.Volume.sum() / baseline)
            typical = (today.High+today.Low+today.Close)/3
            vwap = (typical*today.Volume).cumsum()/today.Volume.cumsum()
            price = float(today.Close.iloc[-1])
            strength = float(price/today.Open.iloc[0]-1-market_return)
            side = "long" if strength > 0 else "short"
            sign = 1 if side == "long" else -1
            turnover = float((typical*today.Volume).sum())
            if relative_volume < 1.5 or turnover < 10_000_000 or sign*(price-vwap.iloc[-1]) <= 0 or sign*(vwap.iloc[-1]-vwap.iloc[-6]) <= 0 or abs(strength) < .002:
                raise ValueError("Activity, VWAP, liquidity or relative-strength filter")
            # Smooth bounded contributions avoid hard ceilings and alphabetical
            # selection among economically different setups. Not a probability.
            components = dict(volume=35*relative_volume/(relative_volume+3),
                              strength=35*abs(strength)/(abs(strength)+.01),
                              liquidity=20*turnover/(turnover+100_000_000), vwap=10.)
            score = sum(components.values())
            rows.append(dict(symbol=symbol, side=side, score=round(score, 2), close=price,
                             ranking_score=score, score_components=components, scoring_version="smooth_v2",
                             relative_volume=round(relative_volume, 2), relative_strength=round(strength*100, 3),
                             vwap=round(float(vwap.iloc[-1]), 2), turnover=round(turnover),
                             selected_at=at.isoformat(), status="WATCH",
                             reason="Unusual same-time volume, relative strength and VWAP agree"))
        except ValueError as exc:
            rejected[symbol] = str(exc)
    ordered = sorted(rows, key=lambda r: (-r["ranking_score"], r["symbol"]))
    return [r for side in ("long", "short") for r in [x for x in ordered if x["side"] == side][:3]], rejected


def entry_signals(frames, benchmark, shortlist, now):
    """Only post-selection confirmations; never retroactively enter a stock."""
    output = []
    config = replace(OrderBlockConfig(), entry_start="09:45", entry_end="14:30", min_reward_risk=2)
    benchmark = benchmark[benchmark.index + pd.Timedelta(minutes=1) <= now]
    for chosen in shortlist:
        frame = frames[chosen["symbol"]]
        frame = frame[frame.index + pd.Timedelta(minutes=1) <= now]
        report = scan(frame, now, config, benchmark=benchmark)
        for setup in report["setups"]:
            if setup["accepted"] and setup["side"] == chosen["side"] and pd.Timestamp(setup["timestamp"]) > pd.Timestamp(chosen["selected_at"]):
                setup = dict(setup, valid_until=(pd.Timestamp(setup['timestamp'])+pd.Timedelta(minutes=3)).isoformat())
                output.append(dict(setup, symbol=chosen["symbol"], status="ENTRY SIGNAL",
                                   execution_status="NOT FILLED", timeframe="1 minute",
                                   reference_volume=float(frame.loc[frame.index < pd.Timestamp(setup['timestamp']), 'Volume'].tail(20).median())))
    return output
