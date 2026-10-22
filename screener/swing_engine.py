"""Causal daily swing signals and portfolio execution; no broker dependencies."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import date
import math

import numpy as np
import pandas as pd

from screener.stock_paper import equity, event, new_account, week
from screener.swing_data import BENCHMARK

VERSION = "expectancy_v3"
STRATEGIES = ("breakout20", "breakout55", "pullback", "contraction")


@dataclass(frozen=True)
class Config:
    strategy: str = "all"
    exit_style: str = "chandelier"
    atr_multiple: float = 3.0
    risk_pct: float = 0.005
    portfolio_risk_pct: float = 0.02
    max_positions: int = 4
    max_weekly: int = 6
    position_cap: float = 0.25
    sector_cap: float = 0.35
    utilization: float = 0.90
    correlation_limit: float = 0.80
    correlated_cap: float = 0.35
    min_turnover: float = 100_000_000
    participation: float = 0.001
    min_score: float = 65
    min_volume: float = 1.2
    max_stop_pct: float = 0.08
    gap_buffer: float = 0.01
    max_gap_atr: float = 0.5
    slippage: float = 0.0005
    brokerage_rate: float = 0.0003
    brokerage_cap: float = 20
    dp_sell_fee: float = 20

    def validate(self):
        if self.strategy not in (*STRATEGIES, "all") or self.exit_style not in ("chandelier", "swing", "ema"):
            raise ValueError("Unknown strategy or trailing exit")
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid {key}")
        if not 0 < self.risk_pct <= self.portfolio_risk_pct <= 0.1:
            raise ValueError("Require 0 < position risk <= portfolio risk <= 10%")
        if not 0 < self.position_cap <= self.utilization <= 1 or not 0 < self.sector_cap <= 1:
            raise ValueError("Invalid capital caps")
        if not 0 < self.correlated_cap <= 1 or not 0 < self.correlation_limit <= 1:
            raise ValueError("Invalid correlation limits")
        if not 0 < self.atr_multiple <= 10 or not 0 < self.max_stop_pct <= 0.25:
            raise ValueError("Invalid stop parameters")
        if not 0 <= self.min_score <= 100 or not 0 < self.participation <= 0.01:
            raise ValueError("Invalid quality/liquidity thresholds")
        if not 1 <= self.max_positions <= 20 or not 1 <= self.max_weekly <= 6:
            raise ValueError("Invalid position or weekly limits")
        if type(self.max_positions) is not int or type(self.max_weekly) is not int:
            raise ValueError("Position limits must be integers")
        if self.min_turnover <= 0 or self.min_volume <= 0 or not 0 <= self.slippage < 0.1 or not 0 <= self.gap_buffer < 1:
            raise ValueError("Invalid liquidity, slippage or gap allowance")
        return self


def costs(value, side, cfg=Config(), day="2026-10-12"):
    """Cash delivery approximation, separately itemized; capital-gains tax excluded."""
    brokerage = min(cfg.brokerage_cap, value * cfg.brokerage_rate)
    # Exchange + IPFT modeled together. Historical schedule changes are explicit.
    exchange_rate = 0.0000345 if day < "2024-10-01" else 0.0000307
    exchange, sebi = value * exchange_rate, value * 0.000001
    result = dict(brokerage=brokerage, exchange_ipft=exchange, sebi=sebi,
                  stt=value * 0.001, stamp=value * 0.00015 if side == "buy" else 0,
                  gst=(brokerage + exchange + sebi) * 0.18,
                  depository=cfg.dp_sell_fee if side == "sell" and value else 0)
    return dict(result, total=sum(result.values()))


def prepare(dataset):
    """Vectorized causal indicators; restore historical share units for execution."""
    frames = dataset["frames"]
    if BENCHMARK not in frames or len(frames[BENCHMARK]) < 250:
        raise ValueError("Nifty 500 benchmark and 250 warmup candles required")
    snapshots, returns = {}, {}
    benchmark_dates = [b["date"] for b in frames[BENCHMARK]]
    previous_benchmark = dict(zip(benchmark_dates[1:], benchmark_dates[:-1]))
    for symbol, bars in frames.items():
        if not bars:
            continue
        df = pd.DataFrame(bars).set_index("date")
        close, high, low = df.close, df.high, df.low
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        ema = close.ewm(span=20, adjust=False).mean()
        features = pd.DataFrame(dict(sma50=close.rolling(50).mean(), sma200=close.rolling(200).mean(),
            sma50_old=close.rolling(50).mean().shift(20), ema20=ema, ema20_prev=ema.shift(),
            atr=tr.rolling(14).mean(), atr_prior=tr.rolling(14).mean().shift(),
            vol20=df.volume.rolling(20).mean().shift(), turnover=(close*df.volume).rolling(20).mean(),
            high20=high.rolling(20).max().shift(), high55=high.rolling(55).max().shift(),
            high250=high.rolling(250).max().shift(), low5=low.rolling(5).min(),
            prev_low=low.shift(), prev_high=high.shift(), prev_close=close.shift(),
            contraction_atr=tr.rolling(5).mean().shift()/tr.rolling(20).mean().shift(),
            contraction_range=(high.rolling(10).max()-low.rolling(10).min()).shift() /
                              (high.rolling(30).max()-low.rolling(30).min()).shift(),
            volatility=close.pct_change(fill_method=None).rolling(20).std()*np.sqrt(252)))
        # Yahoo OHLC already adjusts past splits. Use future factors only to
        # restore day-specific currency/share units, never as signal inputs.
        factors = [1.] * (len(bars)+1)
        for i in range(len(bars)-1, -1, -1):
            factors[i] = factors[i+1] * (bars[i].get("split", 0) or 1)
        previous_stock = dict(zip(df.index[1:], df.index[:-1]))
        returns[symbol] = {day: float(value) for day, value in close.pct_change(fill_method=None).items()
                           if pd.notna(value) and previous_stock.get(day) == previous_benchmark.get(day)}
        price_keys = {"sma50", "sma200", "sma50_old", "ema20", "ema20_prev", "atr", "atr_prior", "high20", "high55", "high250", "low5", "prev_low", "prev_high", "prev_close"}
        for i, (day, row) in enumerate(features.iterrows()):
            if i < 250 or row.isna().any():
                continue
            factor = factors[i+1]
            item = {key: float(value) * factor if key in price_keys else float(value) for key, value in row.items()}
            item.update({key: bars[i][key] * factor for key in ("open", "high", "low", "close")})
            item.update(date=day, volume=bars[i]["volume"]/factor, vol20=item["vol20"]/factor,
                        split=bars[i].get("split", 0), dividend=bars[i].get("dividend", 0)*factor)
            # Relative returns are aligned later on the index calendar.
            snapshots.setdefault(day, {})[symbol] = item
    days = sorted(day for day, snapshot in snapshots.items() if BENCHMARK in snapshot)
    closes = {s: {b["date"]: b["close"] for b in bars} for s, bars in frames.items()}
    indexes = {day: i for i, day in enumerate(benchmark_dates)}
    for day in days:
        first = benchmark_dates[indexes[day]-60]
        for symbol, f in snapshots[day].items():
            f["relative_strength"] = ((closes[symbol][day]/closes[symbol][first]) /
                (closes[BENCHMARK][day]/closes[BENCHMARK][first])-1)*100 if first in closes[symbol] else None
    return dict(days=days, snapshots=snapshots, returns=returns, members=dataset["members"])


def regime(index):
    if index["close"] < index["sma200"] and index["sma50"] < index["sma50_old"]:
        return "BEARISH"
    if index["close"] > index["sma50"] > index["sma200"] and index["sma50"] > index["sma50_old"] and index["volatility"] < .35:
        return "BULLISH"
    return "SIDEWAYS"


def evaluate(member, f, market, cfg, strategy, known_event=None):
    row = dict(symbol=member["symbol"], company=member.get("name", member["symbol"]), sector=member.get("industry", "Unknown"),
               strategy=strategy, status="WATCH", score=0., reason="Missing aligned completed history", close=None,
               as_of=f["date"] if f else None, quantity=0, capital_required=0., score_components={},
               event_risk="UNKNOWN", reward_risk=None, trailing_stop=None)
    if not f:
        return row
    row.update(close=f["close"], relative_strength=f["relative_strength"], volume_ratio=f["volume"]/max(f["vol20"], 1))
    if f["relative_strength"] is None:
        return row
    close, atr = f["close"], f["atr"]
    if known_event and known_event.get("known_at", "9999") <= f["date"]:
        row["event_risk"] = known_event.get("risk", "UNKNOWN")
        if known_event.get("blocked_until", "") >= f["date"]:
            row["reason"] = "Known corporate-event risk; new entry blocked"
            return row
    valid_trend = close > f["sma50"] and f["sma50"] > f["sma50_old"]
    if close < 50 or f["turnover"] < cfg.min_turnover or not valid_trend or market == "BEARISH" or f["relative_strength"] <= 0:
        row["reason"] = "Trend, liquidity, relative-strength or market-regime filter not satisfied"
        return row
    strength = (close-f["low"])/max(f["high"]-f["low"], .01)
    trigger = f["high55"] if strategy == "breakout55" else f["high20"]
    if strategy == "pullback":
        valid = (f["sma50"] > f["sma200"] and f["prev_low"] <= f["ema20_prev"]*1.01
                 and f["prev_close"] >= f["ema20_prev"]*.98 and close > f["prev_high"]
                 and close > f["ema20"] and close-f["ema20"] <= 1.5*atr)
        trigger = f["prev_high"]
    else:
        valid = close > trigger and close-trigger <= atr
        if strategy == "contraction":
            valid = valid and f["contraction_atr"] < .8 and f["contraction_range"] < .65
    required_volume = cfg.min_volume + (.2 if strategy == "contraction" else 0)
    if not valid or row["volume_ratio"] < required_volume or strength < .65:
        row["reason"] = f"Wait for confirmed {strategy} with volume and strong close"
        return row
    stop = min(f["low5"]-.1*atr, close-cfg.atr_multiple*atr)
    risk = close-stop
    if atr <= 0 or not .015 <= risk/close <= cfg.max_stop_pct or stop <= 0:
        row["reason"] = "Structural/ATR stop is outside permitted distance"
        return row
    room = (f["high250"]-close)/risk if f["high250"] > close else None
    components = dict(trend=20 if f["sma50"] > f["sma200"] else 10,
        setup=round(20*strength, 2), relative_strength=min(15, f["relative_strength"]),
        volume=min(10, row["volume_ratio"]*5), market=10 if market == "BULLISH" else 3,
        liquidity=min(10, 5*f["turnover"]/cfg.min_turnover),
        volatility=max(0, 5*(1-risk/close/cfg.max_stop_pct)),
        reward_risk=3 if room is None else min(5, max(0, room)*2),
        events=5 if row["event_risk"] == "CLEAR" else 0)
    score = sum(components.values())
    row.update(score=round(score, 2), score_components=components, entry_trigger=round(trigger+.01, 2),
               planned_entry=close*(1+cfg.slippage), reference=close, initial_stop=stop, stop=stop,
               risk_per_share=risk, max_entry=close+cfg.max_gap_atr*atr, atr=atr,
               turnover=f["turnover"], average_volume=f["vol20"], regime=market,
               reward_risk=round(room, 2) if room is not None else None,
               reward_risk_note="Room to prior 250-session high; not a target" if room is not None else "No known overhead high; trailing payoff is uncapped",
               reference_3r=close+3*risk,
               invalidation="Stop breached, next open below trigger, excessive gap, stale/missing data, regime deterioration or portfolio limits",
               config=asdict(cfg), engine=VERSION)
    if score < cfg.min_score + (5 if market == "SIDEWAYS" else 0):
        row["reason"] = "Valid pattern below setup-quality threshold"
    else:
        row.update(status="BUY CANDIDATE", reason=f"Confirmed {strategy}; trend, relative strength and volume align. Event risk: {row['event_risk']}")
    return row


def correlations(symbol, others, returns, day):
    a = returns.get(symbol, {})
    result = {}
    for other in others:
        b = returns.get(other, {})
        dates = sorted(d for d in a.keys() & b.keys() if d <= day)[-60:]
        if len(dates) < 40:
            result[other] = None
        else:
            x, y = [a[d] for d in dates], [b[d] for d in dates]
            result[other] = float(np.corrcoef(x, y)[0, 1]) if np.std(x) > 0 and np.std(y) > 0 else None
    return result


def open_risk(account, cfg, day):
    return sum(max(0, t["quantity"]*t["mark"] - t["quantity"]*t["stop"]*(1-cfg.slippage)
                   + costs(t["quantity"]*t["stop"], "sell", cfg, day)["total"]
                   + t["quantity"]*t["mark"]*cfg.gap_buffer)
               for t in account["trades"] if t["status"] == "OPEN")


def size(account, candidate, fill, cfg, returns, day):
    active = [t for t in account["trades"] if t["status"] == "OPEN"]
    value = equity(account)
    reason = None
    if any(t["symbol"] == candidate["symbol"] for t in active):
        reason = "Duplicate position"
    elif len(active) >= cfg.max_positions:
        reason = "Maximum simultaneous positions"
    elif sum(week(t["entry_date"]) == week(day) for t in account["trades"]) >= cfg.max_weekly:
        reason = "Weekly filled-entry limit"
    risk_per_share = fill-candidate["stop"] + fill*cfg.gap_buffer
    if risk_per_share <= 0 or (fill-candidate["stop"])/fill > cfg.max_stop_pct:
        return 0, 0., "Actual fill violates stop-distance limit"
    exposure = sum(t["quantity"]*t["mark"] for t in active)
    sector = sum(t["quantity"]*t["mark"] for t in active if t["sector"] == candidate["sector"])
    corr = correlations(candidate["symbol"], [t["symbol"] for t in active], returns, candidate["as_of"])
    # Missing correlation history is treated conservatively as correlated.
    clustered = sum(t["quantity"]*t["mark"] for t in active if corr[t["symbol"]] is None or corr[t["symbol"]] >= cfg.correlation_limit)
    available = min(account["cash"], value*cfg.position_cap, value*cfg.utilization-exposure,
                    value*cfg.sector_cap-sector, value*cfg.correlated_cap-clustered,
                    candidate["turnover"]*cfg.participation)
    budget = min(value*cfg.risk_pct, value*cfg.portfolio_risk_pct-open_risk(account, cfg, day))
    quantity = max(0, math.floor(min(available/fill, budget/risk_per_share,
                                    candidate["average_volume"]*cfg.participation)))
    while quantity > 0:
        buy = costs(quantity*fill, "buy", cfg, day)["total"]
        sell = costs(quantity*candidate["stop"]*(1-cfg.slippage), "sell", cfg, day)["total"]
        estimated = quantity*(fill-candidate["stop"]*(1-cfg.slippage)+fill*cfg.gap_buffer)+buy+sell
        if quantity*fill+buy <= available and estimated <= budget:
            break
        quantity -= 1
    if reason:
        return 0, 0., reason
    return quantity, estimated if quantity else 0., None if quantity else "Cash, portfolio/sector/correlation risk or liquidity limit"


def process_day(account, snapshot, day, returns, cfg):
    if account["last_session"] and day <= account["last_session"]:
        return
    pending = [o for o in account["orders"] if o["status"] == "QUEUED" and o["created_date"] < day]
    active = [t for t in account["trades"] if t["status"] == "OPEN"]
    missing = [x["symbol"] for x in pending+active if x["symbol"] not in snapshot]
    if missing:
        account["warnings"].append(f"Replay paused {day}: missing required completed bars for {', '.join(sorted(set(missing)))}")
        return
    for t in active:
        b = snapshot[t["symbol"]]
        split = b["split"]
        if split and split != 1:
            t["quantity"] *= split
            for key in ("entry", "stop", "initial_stop", "mark", "highest_high", "target"):
                if t.get(key) is not None:
                    t[key] /= split
            event(account, day, t["symbol"], "SPLIT", f"Share units adjusted by {split}")
        dividend = t["quantity"]*b["dividend"]
        account["cash"] += dividend
        t["dividends"] += dividend
        if dividend:
            event(account, day, t["symbol"], "DIVIDEND", f"Ex-date paper credit {dividend:.2f}")
    for order in sorted(pending, key=lambda o: (-o["score"], o["symbol"])):
        b = snapshot[order["symbol"]]
        fill = b["open"]*(1+cfg.slippage)
        reason = None
        if b["split"] or b["volume"] <= 0:
            reason = "Split on entry day or zero traded volume"
        elif b["open"] < order["entry_trigger"] or fill > order["max_entry"]:
            reason = "Next opening price outside confirmed trigger/gap band"
        quantity, risk, risk_reason = size(account, order, fill, cfg, returns, day)
        reason = reason or risk_reason
        if reason:
            order.update(status="SKIPPED", outcome_date=day, outcome_reason=reason)
            event(account, day, order["symbol"], "SKIPPED", reason)
            continue
        fee = costs(quantity*fill, "buy", cfg, day)
        cost = quantity*fill+fee["total"]
        account["cash"] -= cost
        account["trades"].append(dict(id=order["id"], symbol=order["symbol"], sector=order["sector"],
            strategy=order["strategy"], engine=VERSION, config=order["config"], entry_date=day,
            entry=fill, quantity=quantity, cost=cost, entry_costs=fee, stop=order["stop"], initial_stop=order["stop"],
            target=None, highest_high=fill, mark=fill, mark_date=day, status="OPEN", sessions=0,
            dividends=0., planned_risk=risk, exit=None, exit_date=None, net_pnl=None,
            entry_regime=order["regime"], pending_exit=None))
        order.update(status="FILLED", outcome_date=day, outcome_reason="Next-session opening fill with costs and risk recheck")
        event(account, day, order["symbol"], "ENTRY_EXECUTED", f"{quantity} at {fill:.4f}; initial stop {order['stop']:.4f}")
    # Same-day exits never fund earlier opening purchases.
    for t in account["trades"]:
        if t["status"] != "OPEN":
            continue
        b = snapshot[t["symbol"]]
        t["sessions"] += 1
        t.update(mark=b["close"], mark_date=day)
        tc = Config(**t["config"]) if t.get("engine") == VERSION else cfg
        price, why = None, None
        if b["open"] <= t["stop"]:
            price, why = b["open"], "GAP_STOP"
        elif t.get("pending_exit"):
            price, why = b["open"], "TREND_REVERSAL"
        elif b["low"] <= t["stop"]:
            price, why = t["stop"], "TRAILING_STOP" if t["stop"] > t.get("initial_stop", t["stop"]) else "INITIAL_STOP"
        # Existing legacy positions keep their agreed rules.
        elif t.get("engine") != VERSION:
            if t.get("target") and b["high"] >= t["target"]:
                price, why = t["target"], "LEGACY_TARGET"
            elif t["sessions"] >= t.get("hold_sessions", 10):
                price, why = b["close"], "LEGACY_TIME_EXIT"
        if price is not None:
            fill = price*(1-tc.slippage)
            fee = costs(t["quantity"]*fill, "sell", tc, day)
            proceeds = t["quantity"]*fill-fee["total"]
            account["cash"] += proceeds
            t.update(status="EXIT", exit=fill, exit_date=day, exit_reason=why, exit_costs=fee,
                     net_pnl=proceeds+t["dividends"]-t["cost"])
            event(account, day, t["symbol"], "STOP_TRIGGER" if "STOP" in why else "EXIT_SIGNAL_EXECUTED", why)
            event(account, day, t["symbol"], "EXIT_EXECUTED", f"{fill:.4f}; net P&L {t['net_pnl']:.2f}")
            continue
        if t.get("engine") != VERSION:
            continue
        t["highest_high"] = max(t["highest_high"], b["high"])
        if tc.exit_style == "chandelier":
            proposed = t["highest_high"]-tc.atr_multiple*b["atr"]
        elif tc.exit_style == "swing":
            proposed = b["low5"]-.1*b["atr"]
        else:
            proposed = t["stop"]
            if b["close"] < b["ema20"] and b["prev_close"] < b["ema20_prev"]:
                t["pending_exit"] = dict(signal_date=day, reason="Two completed closes below EMA20; exit next open")
                event(account, day, t["symbol"], "EXIT_SIGNAL", t["pending_exit"]["reason"])
        new_stop = max(t["stop"], min(proposed, b["close"]-.01))
        if new_stop > t["stop"]:
            event(account, day, t["symbol"], "STOP_RAISED", f"{t['stop']:.4f} to {new_stop:.4f}, effective next session")
            t["stop"] = new_stop
    account["last_session"] = day
    account["equity_history"].append(dict(date=day, equity=equity(account), cash=account["cash"],
                                          regime=regime(snapshot[BENCHMARK]), open_risk=open_risk(account, cfg, day)))


def scan_day(account, prepared, day, cfg, *, queue=True):
    snapshot = prepared["snapshots"][day]
    market = regime(snapshot[BENCHMARK])
    rows = []
    strategies = STRATEGIES if cfg.strategy == "all" else (cfg.strategy,)
    for member in prepared["members"]:
        for strategy in strategies:
            rows.append(evaluate(member, snapshot.get(member["symbol"]), market, cfg, strategy, member.get("event")))
    rows.sort(key=lambda r: (-r["score"], r["symbol"], r["strategy"]))
    reserved = {t["symbol"] for t in account["trades"] if t["status"] == "OPEN"}
    reserved.update(o["symbol"] for o in account["orders"] if o["status"] == "QUEUED")
    # A shadow account reserves capital and risk for every recommended candidate.
    import copy
    shadow = copy.deepcopy(account)
    for pending in account["orders"]:
        if pending["status"] == "QUEUED" and pending.get("engine") == VERSION:
            shadow["cash"] -= pending["capital_required"]
            shadow["trades"].append(dict(symbol=pending["symbol"], sector=pending["sector"],
                quantity=pending["quantity"], mark=pending["planned_entry"], stop=pending["stop"],
                status="OPEN", entry_date=day))
    for row in rows:
        if row["status"] != "BUY CANDIDATE":
            continue
        if account["warnings"] or row["symbol"] in reserved:
            row.update(status="WATCH", reason="Already held/queued or account replay paused")
            continue
        quantity, risk, reason = size(shadow, row, row["planned_entry"], cfg, prepared["returns"], day)
        if reason:
            row.update(status="WATCH", reason=reason)
            continue
        fill = row["planned_entry"]
        capital = quantity*fill+costs(quantity*fill, "buy", cfg, day)["total"]
        row.update(quantity=quantity, capital_required=capital, estimated_risk=risk,
                   risk_per_share=risk/quantity)
        reserved.add(row["symbol"])
        shadow["cash"] -= capital
        shadow["trades"].append(dict(symbol=row["symbol"], sector=row["sector"], quantity=quantity,
                                    mark=fill, stop=row["stop"], status="OPEN", entry_date=day))
        key = f"{VERSION}:{row['symbol']}:{row['strategy']}:{day}"
        if queue and not any(o["id"] == key for o in account["orders"]):
            account["orders"].append(dict(row, id=key, created_date=day, status="QUEUED"))
            event(account, day, row["symbol"], "ENTRY_SIGNAL", f"{row['strategy']}; next open only, not a filled trade")
    return rows


def simulate(prepared, days, cfg):
    account = new_account()
    for day in days:
        process_day(account, prepared["snapshots"][day], day, prepared["returns"], cfg)
        if account["warnings"]:
            raise ValueError(account["warnings"])
        scan_day(account, prepared, day, cfg)
        assert account["cash"] >= -1e-6
    return account
