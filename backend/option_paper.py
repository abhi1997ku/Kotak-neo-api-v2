"""Local-only bought-option simulator. Market-data GETs only; never broker orders."""
from __future__ import annotations
import json
import math
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import pandas as pd
from backend.indstocks_data import client, INDstocksError, INDstocksData
from backend.indstocks_monitor import ROOT, refresh_master, resolve_future, observe
from backend.option_paper_store import load_state, save_state
from backend.order_block_strategy import STRATEGY, PARAMS, eligible

STATE = ROOT / "ob_option_paper.json"
JOURNAL = ROOT / "ob_option_paper.sqlite"


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
    future_quote = data.get(future_code) or {}
    if future_quote.get('live_price') is None:
        raise ValueError('MISSING_UNDERLYING_QUOTE')
    future = float(future_quote['live_price'])
    if not math.isfinite(future) or future <= 0:
        raise ValueError("INVALID_UNDERLYING")
    setups = eligible(state, setups, now, execution_event)
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
        if sign*(future-setup["entry"]) > 0:
            execution_event(state, key, 'WAITING_ENTRY_TRIGGER', now)
            continue
        actual_risk = sign*(future-setup['stop'])
        if actual_risk < PARAMS.min_stop_points or actual_risk > PARAMS.max_stop_points:
            execution_event(state, key, 'STOP_TOO_TIGHT' if actual_risk < PARAMS.min_stop_points else 'STOP_TOO_WIDE', now)
            state['seen'].append(key)
            continue
        spot_quote = data.get('NIDX_40000003') or {}
        if spot_quote.get('live_price') is None:
            raise ValueError('MISSING_SPOT_QUOTE')
        spot = float(spot_quote['live_price'])
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
        actual_risk = sign*(future-setup["stop"])
        state["position"] = {**setup, **contract, "risk": actual_risk,
            "target": future+sign*PARAMS.target_r*actual_risk, "strategy": STRATEGY, "entered_at": now.isoformat(), "premium": premium,
            "underlying_fill": future, "entry_snapshot": snapshot, "result": "OPEN_ESTIMATE",
            "quote_basis": "LOCAL_RECEIPT_UNVERIFIED_EXCHANGE_TIME", "gross_pnl": None,
            "costs_included": False, "mark_stale": True}
        for candidate in setups:
            candidate_key = candidate['timestamp']+':'+candidate['side']
            if candidate_key not in state['seen']:
                state['seen'].append(candidate_key)
            if candidate_key != key:
                execution_event(state, candidate_key, 'CANCELLED_ON_FILL', now)
        execution_event(state, key, 'FILLED', now)
        state.pop("needed_option", None)
        return


def fetch_quotes(provider, codes: list[str]) -> dict:
    """Reject slow responses even when their local receipt timestamp is recent."""
    start = time.monotonic()
    snapshot = provider.quotes(codes)
    if time.monotonic()-start > 3:
        raise ValueError('SLOW_QUOTE_RESPONSE')
    return snapshot


def prepare_options(state: dict, setups: list[dict], data: dict, rows: list[dict], now: pd.Timestamp) -> list[str]:
    """Prepare both eligible sides before a touch; never invent missing depth."""
    if state.get('position'):
        return [state['position']['code']]
    candidates = eligible(state, setups, now, execution_event)
    spot = float(data.get('NIDX_40000003', {}).get('live_price', 0))
    if not math.isfinite(spot) or spot <= 0:
        return []
    return sorted({select_contract(rows, spot, s['side'], now)['code'] for s in candidates})


def record_feed_wait(state: dict, setups: list[dict], now: pd.Timestamp) -> None:
    """Do not overwrite filled, cancelled, expired or superseded decisions."""
    for setup in setups:
        key = setup['timestamp']+':'+setup['side']
        if key not in state.get('seen', []) and pd.Timestamp(setup['timestamp']) <= now < pd.Timestamp(setup['valid_until']):
            execution_event(state, key, 'WAITING_FRESH_UNDERLYING_TICK', now)


def tick_is_fresh(latest: dict | None, now: pd.Timestamp, connected: bool) -> bool:
    """Recheck freshness at execution, after any intervening processing."""
    return bool(connected and latest is not None and
                0 <= (now-pd.Timestamp(latest['received_at'])).total_seconds() <= 3)


def main() -> None:
    """Run independent paper estimates; recover open positions after process restart."""
    ROOT.mkdir(exist_ok=True)
    guard = sqlite3.connect(ROOT/"ob_option_paper_lock.sqlite", timeout=0)
    guard.execute("CREATE TABLE IF NOT EXISTS lock(id INTEGER)")
    guard.commit()
    guard.execute("BEGIN EXCLUSIVE")
    state = load_state(JOURNAL, STATE)
    state["started_at"] = pd.Timestamp.now(tz="Asia/Kolkata").isoformat()
    if state.get("position"):
        state["position"]["data_gap"] = True
    last_observe = 0.0
    ticks = None
    history_pool = ThreadPoolExecutor(max_workers=1)
    quote_pool = ThreadPoolExecutor(max_workers=1)
    history_client, quote_client = INDstocksData(), INDstocksData()
    history_job = quote_job = None
    quote_cache = None
    active_token = None
    while True:
        now = pd.Timestamp.now(tz="Asia/Kolkata")
        try:
            if now.weekday() >= 5 or not "09:15" <= now.strftime("%H:%M") <= "15:30":
                state["status"] = "OUTSIDE_SESSION"
                state.pop('error', None)
                state['tick_status'] = 'OUTSIDE_SESSION'
                if ticks is not None:
                    ticks.stop()
                    ticks = None
            else:
                # Reload only the market-data token, never broker credentials.
                from dotenv import dotenv_values
                import os
                token = dotenv_values(Path(__file__).resolve().parents[1]/".env").get("INDSTOCKS_ACCESS_TOKEN")
                if token:
                    os.environ["INDSTOCKS_ACCESS_TOKEN"] = token
                    if active_token is not None and token != active_token:
                        if ticks is not None:
                            ticks.stop()
                            ticks = None
                        quote_cache = None
                    active_token = token
                refresh_master(now)
                future = resolve_future(now)
                state["future_code"] = "NFO_"+future["SECURITY_ID"]
                if ticks is None:
                    from backend.paper_ticks import PaperTicks
                    ticks = PaperTicks(state['future_code'])
                elif ticks.code != state['future_code']:
                    if state.get('position'):
                        raise ValueError('OPEN_POSITION_CONTRACT_ROLLOVER_REQUIRES_REVIEW')
                    ticks.stop()
                    from backend.paper_ticks import PaperTicks
                    ticks = PaperTicks(state['future_code'])
                    quote_cache = None
                if history_job is not None and history_job.done():
                    try:
                        monitor_state = history_job.result()
                        state['current_session_bars'] = monitor_state['today_bars']
                        monitor_state.update(mode='option-paper-estimates', fill_blocker=None)
                        status_tmp = ROOT/'indstocks_ob_status.tmp'
                        status_tmp.write_text(json.dumps(monitor_state))
                        status_tmp.replace(ROOT/'indstocks_ob_status.json')
                        state.pop('history_error', None)
                    except Exception as exc:
                        state['history_error'] = str(exc) if isinstance(exc, (INDstocksError, ValueError)) else type(exc).__name__
                    history_job = None
                if history_job is None and time.monotonic()-last_observe >= 10:
                    history_job = history_pool.submit(observe, ROOT/'indstocks_ob_v3_observations.sqlite', now, history_client)
                    last_observe = time.monotonic()
                with sqlite3.connect(ROOT/"indstocks_ob_v3_observations.sqlite") as db:
                    db.execute("CREATE TABLE IF NOT EXISTS observations (contract TEXT, timestamp TEXT, side TEXT, accepted INTEGER, payload TEXT, observed_at TEXT, PRIMARY KEY(contract,timestamp,side))")
                    setups = [json.loads(x[0]) for x in db.execute("SELECT payload FROM observations WHERE accepted=1 AND contract=? ORDER BY timestamp", (state["future_code"],))]
                buffered, latest, fresh = ticks.drain()
                state['tick_status'] = 'FRESH' if fresh else 'STALE_OR_DISCONNECTED'
                state['stream_health'] = ticks.health()
                state['last_tick_at'] = latest['received_at'] if latest else None
                if state.get('position'):
                    for tick in buffered:
                        latch_exit(state['position'], tick['price'], pd.Timestamp(tick['received_at']).tz_convert('Asia/Kolkata'))
                    save_state(JOURNAL, state)
                if quote_job is not None and quote_job.done():
                    try:
                        quote_cache = quote_job.result()
                        state.pop('quote_error', None)
                    except Exception as exc:
                        quote_cache = None
                        state['quote_error'] = str(exc) if isinstance(exc, (INDstocksError, ValueError)) else type(exc).__name__
                    quote_job = None
                rows = json.loads((ROOT/'indstocks_fno.json').read_text())
                codes = [state['future_code'], 'NIDX_40000003']
                cached_data = quote_cache['data'] if quote_cache else {}
                codes += prepare_options(state, setups, cached_data, rows, now)
                if state.get('needed_option') and state['needed_option'] not in codes:
                    codes.append(state['needed_option'])
                if quote_job is None:
                    quote_job = quote_pool.submit(fetch_quotes, quote_client, codes)
                if quote_cache is None:
                    raise ValueError(state.get('quote_error', 'WAITING_OPTION_SNAPSHOT'))
                snapshot = deepcopy(quote_cache)
                # Quote futures may complete after the loop-start timestamp.
                now = pd.Timestamp.now(tz="Asia/Kolkata")
                if (now-pd.Timestamp(snapshot['received_at'])).total_seconds() > 3:
                    raise ValueError('STALE_OPTION_SNAPSHOT')
                fresh = tick_is_fresh(latest, now, fresh)
                if fresh:
                    snapshot['rest_underlying_price'] = (snapshot['data'].get(state['future_code']) or {}).get('live_price')
                    snapshot['data'][state['future_code']] = {**(snapshot['data'].get(state['future_code']) or {}), 'live_price': latest['price']}
                    snapshot['underlying_tick'] = latest
                    state['execution_source'] = 'STREAM_TRIGGER_REST_OPTION_DEPTH'
                elif not state.get('position'):
                    record_feed_wait(state, setups, now)
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
        state['execution_version'] = 'ob-midpoint-v3'
        state['strategy'] = STRATEGY
        # A journal failure stops this worker before it can simulate another fill.
        save_state(JOURNAL, state)
        temporary = STATE.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, allow_nan=False))
        temporary.replace(STATE)
        time.sleep(0.25 if state['status'] in ('RUNNING_ESTIMATED_PAPER', 'DATA_BLOCKED') else 30)


if __name__ == "__main__":
    main()
