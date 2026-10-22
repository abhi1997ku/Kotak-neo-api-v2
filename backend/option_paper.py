"""Local-only bought-option simulator. Market-data GETs only; never broker orders."""
from __future__ import annotations
import json
import math
import sqlite3
import time
from pathlib import Path
import pandas as pd
from backend.indstocks_data import client, INDstocksError
from backend.indstocks_monitor import ROOT, refresh_master, resolve_future, observe
from backend.option_paper_store import load_state, save_state

STATE = ROOT / "ema9_option_paper.json"
JOURNAL = ROOT / "ema9_option_paper.sqlite"


def execution_event(state: dict, key: str, reason: str, now: pd.Timestamp) -> None:
    """Keep the latest decision plus a durable transition history per setup."""
    decisions = state.setdefault('execution_decisions', {})
    if decisions.get(key, {}).get('reason') != reason:
        event = {'setup': key, 'reason': reason, 'at': now.isoformat()}
        decisions[key] = event
        state.setdefault('execution_events', []).append(event)


def latch_exit(position: dict, price: float, at: pd.Timestamp) -> None:
    """Retain the first observed crossing even if price recovers during a depth fetch."""
    if at < pd.Timestamp(position['entered_at']):
        return
    sign = 1 if position['side'] == 'long' else -1
    reason = ('SESSION_EXIT' if at.strftime('%H:%M') >= '15:15' or at.date() != pd.Timestamp(position['entered_at']).date()
              else 'UNDERLYING_STOP' if sign * (price-position['stop']) <= 0
              else 'UNDERLYING_2R_TARGET' if sign * (price-position['target']) >= 0 else None)
    if reason and not position.get('exit_reason'):
        position.update(exit_reason=reason, exit_trigger_at=at.isoformat(), exit_trigger_price=price)


def select_contract(rows: list[dict], spot: float, side: str, now: pd.Timestamp) -> dict:
    """Choose nearest expiry and two listed strikes ITM from spot ATM."""
    kind = "CE" if side == "long" else "PE"
    chain = [r for r in rows if r.get("EXCH") == "NSE" and r.get("INSTRUMENT_NAME") == "OPTIDX"
             and r.get("TRADING_SYMBOL", "").startswith("BANKNIFTY-") and r.get("OPTION_TYPE") == kind
             and pd.Timestamp(r["EXPIRY_DATE"]).date() >= now.date()]
    if not chain:
        raise ValueError("NO_CONTRACT")
    expiry = min(pd.Timestamp(r["EXPIRY_DATE"]) for r in chain)
    chain = [r for r in chain if pd.Timestamp(r["EXPIRY_DATE"]) == expiry]
    strikes = sorted({float(r["STRIKE_PRICE"]) for r in chain})
    atm = min(strikes, key=lambda x: (abs(x-spot), x))
    index = strikes.index(atm) + (-2 if kind == "CE" else 2)
    if not 0 <= index < len(strikes):
        raise ValueError("INCOMPLETE_STRIKE_LADDER")
    row = next(r for r in chain if float(r["STRIKE_PRICE"]) == strikes[index])
    quantity = int(float(row["LOT_UNITS"]))*2
    if quantity <= 0:
        raise ValueError("INVALID_LOT_SIZE")
    return {"code": "NFO_"+row["SECURITY_ID"], "symbol": row["TRADING_SYMBOL"],
            "strike": strikes[index], "option_type": kind, "quantity": quantity, "expiry": str(expiry)}


def depth_fill(quote: dict, code: str, quantity: int, buying: bool) -> float:
    """Consume displayed depth; refuse insufficient size or invalid/crossed books."""
    depth = quote.get("market_depth", {})
    levels = depth.get(code, depth).get("depth", [])
    number = lambda x: float(str(x).replace(",", ""))
    if not levels or quantity <= 0:
        raise ValueError("NO_DEPTH")
    bid, ask = number(levels[0]["buy"]["price"]), number(levels[0]["sell"]["price"])
    if not all(math.isfinite(x) and x > 0 for x in (bid, ask)) or bid > ask:
        raise ValueError("INVALID_BOOK")
    if buying and ask-bid > 3:
        raise ValueError("SPREAD_TOO_WIDE")
    remaining, total = quantity, 0.0
    for level in levels:
        item = level["sell" if buying else "buy"]
        price, size = number(item["price"]), number(item["quantity"])
        if not all(math.isfinite(x) and x >= 0 for x in (price, size)) or price == 0:
            raise ValueError("INVALID_DEPTH")
        take = min(remaining, size)
        total += take*price
        remaining -= take
        if remaining == 0:
            return total/quantity
    raise ValueError("INSUFFICIENT_DEPTH")


def process(state: dict, setups: list[dict], snapshot: dict, rows: list[dict], now: pd.Timestamp) -> None:
    """Advance pending/open positions using one newly fetched snapshot, never candles."""
    age = (now-pd.Timestamp(snapshot["received_at"])).total_seconds()
    if not 0 <= age <= 3:
        raise ValueError("STALE_RECEIPT")
    data = snapshot["data"]
    future_code = state["future_code"]
    future = float(data[future_code]["live_price"])
    if not math.isfinite(future) or future <= 0:
        raise ValueError("INVALID_UNDERLYING")
    position = state.get("position")
    if position:
        latch_exit(position, future, now)
        quote = data.get(position["code"])
        if quote is None:
            position["mark_stale"] = True
            return
        bid = depth_fill(quote, position["code"], position["quantity"], False)
        position.update(mark=bid, mark_at=now.isoformat(), mark_stale=False,
                        gross_pnl=round((bid-position["premium"])*position["quantity"], 2))
        if position.get("exit_reason"):
            position.update(exited_at=now.isoformat(), exit_premium=bid, exit_snapshot=snapshot,
                            result="CLOSED_ESTIMATE")
            state["trades"].append(position)
            state["position"] = None
        return
    if now.weekday() >= 5 or not "09:30" <= now.strftime("%H:%M") < "15:00":
        return
    for setup in setups:
        key = setup["timestamp"]+":"+setup["side"]
        if key in state["seen"]:
            continue
        stamp = pd.Timestamp(setup["timestamp"])
        # Never backfill a historical entry after startup/reconnection.
        if stamp < pd.Timestamp(state["started_at"]) or now >= pd.Timestamp(setup["valid_until"]):
            execution_event(state, key, 'BEFORE_START_OR_RECOVERY' if stamp < pd.Timestamp(state['started_at']) else 'ENTRY_EXPIRED', now)
            state["seen"].append(key)
            continue
        if stamp > now:
            continue
        sign = 1 if setup["side"] == "long" else -1
        if sign*(future-setup["stop"]) <= 0:
            execution_event(state, key, 'INVALIDATED_BEFORE_ENTRY', now)
            state["seen"].append(key)
            continue
        if sign*(future-setup["entry"]) < 0:
            execution_event(state, key, 'WAITING_ENTRY_TRIGGER', now)
            continue
        if sign*(setup["target"]-future) <= 0:
            execution_event(state, key, 'TARGET_ALREADY_PASSED', now)
            state["seen"].append(key)
            continue
        spot = float(data["NIDX_40000003"]["live_price"])
        if not math.isfinite(spot) or spot <= 0:
            raise ValueError("INVALID_SPOT")
        contract = select_contract(rows, spot, setup["side"], now)
        if contract["code"] not in data:
            execution_event(state, key, 'WAITING_OPTION_DEPTH', now)
            state["needed_option"] = contract["code"]
            return  # Fetch a new joint snapshot before considering a fill.
        try:
            premium = depth_fill(data[contract["code"]], contract["code"], contract["quantity"], True)
        except ValueError as exc:
            execution_event(state, key, str(exc), now)
            continue
        state["position"] = {**setup, **contract, "entered_at": now.isoformat(), "premium": premium,
            "underlying_fill": future, "entry_snapshot": snapshot, "result": "OPEN_ESTIMATE",
            "quote_basis": "LOCAL_RECEIPT_UNVERIFIED_EXCHANGE_TIME", "gross_pnl": None,
            "costs_included": False, "mark_stale": True}
        state["seen"].append(key)
        execution_event(state, key, 'FILLED', now)
        state.pop("needed_option", None)
        return


def main() -> None:
    """Run independent paper estimates; recover open positions after process restart."""
    ROOT.mkdir(exist_ok=True)
    guard = sqlite3.connect(ROOT/"ema9_option_paper_lock.sqlite", timeout=0)
    guard.execute("CREATE TABLE IF NOT EXISTS lock(id INTEGER)")
    guard.commit()
    guard.execute("BEGIN EXCLUSIVE")
    state = load_state(JOURNAL, STATE)
    state["started_at"] = pd.Timestamp.now(tz="Asia/Kolkata").isoformat()
    if state.get("position"):
        state["position"]["data_gap"] = True
    last_observe = 0.0
    ticks = None
    while True:
        now = pd.Timestamp.now(tz="Asia/Kolkata")
        try:
            if now.weekday() >= 5 or not "09:15" <= now.strftime("%H:%M") <= "15:30":
                state["status"] = "OUTSIDE_SESSION"
            else:
                # Reload only the market-data token, never broker credentials.
                from dotenv import dotenv_values
                import os
                token = dotenv_values(Path(__file__).resolve().parents[1]/".env").get("INDSTOCKS_ACCESS_TOKEN")
                if token:
                    os.environ["INDSTOCKS_ACCESS_TOKEN"] = token
                refresh_master(now)
                future = resolve_future(now)
                state["future_code"] = "NFO_"+future["SECURITY_ID"]
                if ticks is None:
                    from backend.paper_ticks import PaperTicks
                    ticks = PaperTicks(state['future_code'])
                elif ticks.code != state['future_code']:
                    raise ValueError('STREAM_CONTRACT_CHANGED_RESTART_REQUIRED')
                if time.monotonic()-last_observe >= 60 and not state.get("position"):
                    monitor_state = observe(ROOT/"indstocks_ema9_observations.sqlite", now)
                    state["current_session_bars"] = monitor_state["today_bars"]
                    monitor_state["mode"] = "option-paper-estimates"
                    monitor_state["fill_blocker"] = None
                    status_tmp = ROOT/"indstocks_ema9_status.tmp"
                    status_tmp.write_text(json.dumps(monitor_state))
                    status_tmp.replace(ROOT/"indstocks_ema9_status.json")
                    last_observe = time.monotonic()
                with sqlite3.connect(ROOT/"indstocks_ema9_observations.sqlite") as db:
                    setups = [json.loads(x[0]) for x in db.execute("SELECT payload FROM observations WHERE accepted=1 ORDER BY timestamp")]
                for setup in setups:
                    key = setup['timestamp']+':'+setup['side']
                    if key in state['seen'] or pd.Timestamp(setup['timestamp']) > now:
                        continue
                    if now >= pd.Timestamp(setup['valid_until']):
                        execution_event(state, key, 'ENTRY_EXPIRED', now)
                        state['seen'].append(key)
                    elif state.get('position'):
                        execution_event(state, key, 'POSITION_ALREADY_OPEN', now)
                codes = [state["future_code"], "NIDX_40000003"]
                if state.get("position"):
                    codes.append(state["position"]["code"])
                elif state.get("needed_option"):
                    codes.append(state["needed_option"])
                start = time.monotonic()
                snapshot = client.quotes(codes)
                if time.monotonic()-start > 3:
                    raise ValueError("SLOW_QUOTE_RESPONSE")
                now = pd.Timestamp.now(tz="Asia/Kolkata")
                buffered, latest, fresh = ticks.drain()
                state['tick_status'] = 'FRESH' if fresh else 'STALE_OR_DISCONNECTED'
                state['last_tick_at'] = latest['received_at'] if latest else None
                if state.get('position'):
                    for tick in buffered:
                        latch_exit(state['position'], tick['price'], pd.Timestamp(tick['received_at']).tz_convert('Asia/Kolkata'))
                    # Commit the crossing before attempting the option fill.
                    save_state(JOURNAL, state)
                if fresh:
                    snapshot['rest_underlying_price'] = snapshot['data'][state['future_code']]['live_price']
                    snapshot['data'][state['future_code']]['live_price'] = latest['price']
                    snapshot['underlying_tick'] = latest
                    state['execution_source'] = 'STREAM_TRIGGER_REST_OPTION_DEPTH'
                elif not state.get('position'):
                    for setup in setups:
                        if pd.Timestamp(setup['timestamp']) <= now < pd.Timestamp(setup['valid_until']):
                            execution_event(state, setup['timestamp']+':'+setup['side'], 'WAITING_FRESH_UNDERLYING_TICK', now)
                    raise ValueError('WAITING_FRESH_UNDERLYING_TICK')
                else:
                    state['execution_source'] = 'REST_EXIT_FALLBACK'
                    state['position']['data_gap'] = True
                process(state, setups, snapshot, json.loads((ROOT/"indstocks_fno.json").read_text()), now)
                state["status"] = "RUNNING_ESTIMATED_PAPER" if state.get("current_session_bars", 0) else "WAITING_CURRENT_SESSION_CANDLES"
                state.pop("error", None)
        except (INDstocksError, ValueError, KeyError, TypeError, sqlite3.Error, OSError) as exc:
            state["status"] = "DATA_BLOCKED"
            state["error"] = str(exc) if isinstance(exc, (INDstocksError, ValueError)) else type(exc).__name__
            execution_event(state, 'DATA', state['error'], pd.Timestamp.now(tz='Asia/Kolkata'))
            # Following a gap, no retrospective entries; open positions are flagged.
            if state.get("position"):
                state["position"].update(data_gap=True, mark_stale=True)
        state.update(checked_at=now.isoformat(), orders_enabled=False, estimates_only=True)
        state['execution_version'] = 'stream-triggers-v2'
        # A journal failure stops this worker before it can simulate another fill.
        save_state(JOURNAL, state)
        temporary = STATE.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, allow_nan=False))
        temporary.replace(STATE)
        time.sleep(0.5 if state['status'] == 'RUNNING_ESTIMATED_PAPER' else 2 if state['status'] == 'DATA_BLOCKED' else 30)


if __name__ == "__main__":
    main()
