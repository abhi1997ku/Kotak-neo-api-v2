"""Broker-free stock paper portfolio. Scan ideas and executed trades are distinct.

Execution uses completed daily bars, next-session opens, adverse slippage and
stop-first ambiguity. Nothing in this module imports a broker or submits orders.
"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = Path(__file__).resolve().parent / "training_data" / "stock_paper_v1.sqlite3"
INITIAL_CAPITAL = 100_000.0
FEE = 0.001  # Simulation assumption, per side; not a broker tariff.
SLIPPAGE = 0.0005
MAX_WEEKLY = 6
MAX_OPEN = 4


def week(day: str) -> str:
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def new_account() -> dict:
    return {"cash": INITIAL_CAPITAL, "orders": [], "trades": [], "events": [],
            "equity_history": [], "last_session": None, "last_scan": None,
            "latest_scan": None, "enabled": False, "warnings": []}


def equity(account: dict) -> float:
    return account["cash"] + sum(t["quantity"] * t["mark"] for t in account["trades"] if t["status"] == "OPEN")


def event(account: dict, day: str, symbol: str, kind: str, detail: str) -> None:
    account["events"].append({"date": day, "symbol": symbol, "kind": kind, "detail": detail})


def evaluate_stock(symbol: str, company: str, sector: str, bars: list[dict], benchmark: list[dict]) -> dict:
    """One transparent confirmed-breakout strategy; scores are ranks, not odds."""
    result = {"symbol": symbol, "company": company, "sector": sector,
              "status": "REJECTED", "reason": "Insufficient daily history", "score": 0,
              "close": None, "as_of": bars[-1]["date"] if bars else None}
    if len(bars) < 200 or len(benchmark) < 200:
        return result
    latest = bars[-1]
    close = latest["close"]
    result["close"] = close
    if latest["date"] != benchmark[-1]["date"]:
        result["reason"] = "Stock data does not match the latest benchmark session"
        return result
    closes = [b["close"] for b in bars]
    sma50, sma200 = sum(closes[-50:]) / 50, sum(closes[-200:]) / 200
    benchmark_close = benchmark[-1]["close"]
    if benchmark_close <= sum(b["close"] for b in benchmark[-50:]) / 50:
        result["reason"] = "Nifty 50 below its 50-day average; preserve cash"
        return result
    volume_avg = sum(b["volume"] for b in bars[-21:-1]) / 20
    turnover = sum(b["volume"] * b["close"] for b in bars[-20:]) / 20
    if close < 50 or turnover < 100_000_000 or volume_avg <= 0:
        result["reason"] = "Below ₹50 or below ₹10 crore average daily turnover"
        return result
    if not close > sma50 > sma200:
        result["reason"] = "Price / 50-day / 200-day trend is not aligned"
        return result
    # Align return windows exactly; stale/missing bars must not improve relative strength.
    by_day = {b["date"]: b for b in bars}
    start = benchmark[-21]["date"]
    if start not in by_day:
        result["reason"] = "Missing aligned relative-strength history"
        return result
    relative = (close / by_day[start]["close"] - benchmark_close / benchmark[-21]["close"]) * 100
    volume_ratio = latest["volume"] / volume_avg
    range_size = latest["high"] - latest["low"]
    close_strength = (close - latest["low"]) / range_size if range_size else 0
    prior_high = max(b["high"] for b in bars[-21:-1])
    result.update(relative_strength=round(relative, 2), relative_volume=round(volume_ratio, 2))
    if close <= prior_high or volume_ratio < 1.3 or close_strength < 0.7 or relative <= 0:
        result.update(status="WATCH", reason="Wait: close above 20-day high, volume ≥1.3×, strong close and positive relative strength")
        return result
    atr = sum(max(b["high"] - b["low"], abs(b["high"] - prev["close"]), abs(b["low"] - prev["close"]))
              for prev, b in zip(bars[-15:-1], bars[-14:])) / 14
    stop = min(b["low"] for b in bars[-5:]) - 0.1 * atr
    risk = close - stop
    if not 0.02 <= risk / close <= 0.08 or close - prior_high > atr:
        result["reason"] = "Stop distance outside 2–8%, or breakout extended by more than one ATR"
        return result
    score = min(100, 40 + min(20, relative * 2) + min(20, (volume_ratio - 1) * 15) + 20 * close_strength)
    result.update(status="QUALIFIED", reason="Confirmed liquid breakout with relative strength; next-session open only",
                  score=round(score, 1), reference=round(close, 2), stop=round(stop, 2),
                  target=round(close + 3 * risk, 2), max_entry=round(close + 0.5 * risk, 2))
    return result


def advance(account: dict, frames: dict[str, list[dict]], sessions: list[str], *, exit_handler=None) -> None:
    """Replay only sessions after setup creation, chronologically across symbols.

    Missing any active symbol's bar blocks that session for the whole account:
    this avoids unknowingly reusing cash or guessing an execution through a gap.
    """
    lookup = {symbol: {b["date"]: b for b in bars} for symbol, bars in frames.items()}
    for day in sorted(set(sessions)):
        if account["last_session"] and day <= account["last_session"]:
            continue
        active = [t for t in account["trades"] if t["status"] == "OPEN"]
        pending = [o for o in account["orders"] if o["status"] == "QUEUED" and o["created_date"] < day]
        required = {item["symbol"] for item in active + pending}
        missing = sorted(s for s in required if day not in lookup.get(s, {}))
        if missing:
            account["warnings"].append(f"Replay paused at {day}: missing bars for {', '.join(missing)}. No fills guessed.")
            break
        for trade in active:
            bar = lookup[trade["symbol"]][day]
            split = bar.get("split", 0)
            if split and split != 1:
                trade["quantity"] *= split
                for key in ("entry", "stop", "target", "mark"):
                    trade[key] /= split
                if "trail_peak" in trade:
                    trade["trail_peak"] /= split
                event(account, day, trade["symbol"], "SPLIT", f"Adjusted paper quantity and levels by {split}")
            dividend = bar.get("dividend", 0) * trade["quantity"]
            account["cash"] += dividend
            trade["dividends"] += dividend
            if dividend:
                event(account, day, trade["symbol"], "DIVIDEND", f"Paper ex-date credit ₹{dividend:.2f}")

        # Opening entries precede same-day exits: exit proceeds cannot fund an
        # earlier opening fill. Existing marks are previous-session prices.
        for order in sorted(pending, key=lambda o: (-o["score"], o["symbol"])):
            symbol = order["symbol"]
            bar = lookup[symbol][day]
            live = [t for t in account["trades"] if t["status"] == "OPEN"]
            count = sum(week(t["entry_date"]) == week(day) for t in account["trades"])
            fill = bar["open"] * (1 + SLIPPAGE)
            risk = fill - order["stop"]
            target = fill * (1 + order["target_return_pct"] / 100) if "target_return_pct" in order else order["target"]
            reason = None
            if bar["volume"] <= 0:
                reason = "No traded volume in the entry session"
            elif bar.get("split", 0):
                reason = "Corporate split on entry session; setup requires a fresh scan"
            elif count >= MAX_WEEKLY:
                reason = "Six filled entries already recorded this week"
            elif len(live) >= MAX_OPEN:
                reason = "Four positions already open"
            elif any(t["symbol"] == symbol for t in live):
                reason = "Symbol already held"
            elif sum(t["sector"] == order["sector"] for t in live) >= 2:
                reason = "Two positions already held in this sector"
            elif bar["open"] < order["reference"] * 0.99 or fill > order["max_entry"] or risk <= 0:
                reason = "Opening gap outside permitted entry band"
            elif "max_stop_pct" in order and risk / fill * 100 > order["max_stop_pct"]:
                reason = "Actual opening fill exceeds the maximum planned stop distance"
            elif (target - fill) / risk < 2:
                reason = "Less than 2R remaining at the actual opening fill"
            value = equity(account)
            stop_fill = order["stop"] * (1 - SLIPPAGE)
            loss_per_share = fill * (1 + FEE) - stop_fill * (1 - FEE)
            quantity = max(0, math.floor(min(min(750, value * 0.0075) / max(loss_per_share, 0.01),
                                              value * 0.25 / (fill * (1 + FEE)),
                                              account["cash"] / (fill * (1 + FEE)))))
            if not reason and quantity < 1:
                reason = "Insufficient available cash or risk budget"
            if reason:
                order.update(status="SKIPPED", outcome_date=day, outcome_reason=reason)
                event(account, day, symbol, "SKIPPED", reason)
                continue
            cost = quantity * fill * (1 + FEE)
            account["cash"] -= cost
            trade = {"id": order["id"], "symbol": symbol, "sector": order["sector"],
                     "entry_date": day, "entry": fill, "quantity": quantity,
                     "stop": order["stop"], "target": target, "mark": fill,
                     "mark_date": day, "cost": cost, "status": "OPEN", "sessions": 0,
                     "dividends": 0.0, "planned_risk": quantity * loss_per_share,
                     "exit": None, "exit_date": None, "net_pnl": None}
            if "strategy" in order:
                trade.update(strategy=order["strategy"], hold_sessions=order["hold_sessions"],
                             target_return_pct=order["target_return_pct"], max_stop_pct=order["max_stop_pct"])
            account["trades"].append(trade)
            order.update(status="FILLED", outcome_date=day, outcome_reason="Next-session open, including slippage")
            event(account, day, symbol, "ENTRY", f"{quantity} shares at ₹{fill:.2f}; costs ₹{cost:.2f}")

        for trade in account["trades"]:
            if trade["status"] != "OPEN":
                continue
            bar = lookup[trade["symbol"]][day]
            trade["sessions"] += 1
            trade.update(mark=bar["close"], mark_date=day)
            # Optional offline-research hook. Production callers omit it and
            # continue to use the unchanged stop / target / ten-session exit.
            if exit_handler is not None:
                exit_handler(account, trade, bar, day)
                continue
            exit_price, status = None, None
            if bar["low"] <= trade["stop"]:
                exit_price, status = min(bar["open"], trade["stop"]), "STOP"
            elif bar["high"] >= trade["target"]:
                exit_price, status = trade["target"], "TARGET"
            elif trade["sessions"] >= trade.get("hold_sessions", 10):
                exit_price, status = bar["close"], "TIME_EXIT"
            if exit_price is not None:
                fill = exit_price * (1 - SLIPPAGE)
                proceeds = trade["quantity"] * fill * (1 - FEE)
                account["cash"] += proceeds
                trade.update(status=status, exit=fill, exit_date=day,
                             net_pnl=proceeds + trade["dividends"] - trade["cost"])
                event(account, day, trade["symbol"], status, f"Exit ₹{fill:.2f}; net P&L ₹{trade['net_pnl']:.2f}")
        account["last_session"] = day
        account["equity_history"].append({"date": day, "equity": equity(account), "cash": account["cash"]})


def queue_setups(account: dict, candidates: list[dict], today: str, stamp: str) -> None:
    """Queue ranked ideas with the same deduplication and capacity rules."""
    busy = {t["symbol"] for t in account["trades"] if t["status"] == "OPEN"}
    busy.update(o["symbol"] for o in account["orders"] if o["status"] == "QUEUED")
    ids = {o["id"] for o in account["orders"]}
    queued = sum(o["status"] == "QUEUED" for o in account["orders"])
    for candidate in candidates:
        key = f"{candidate['symbol']}:{candidate['as_of']}"
        if candidate["status"] != "QUALIFIED":
            continue
        if account["warnings"]:
            candidate["reason"] = "Qualified, but account replay is paused for missing data"
        elif candidate["symbol"] in busy or key in ids:
            candidate["reason"] = "Qualified; already held or this setup has already been processed"
        elif queued >= MAX_WEEKLY:
            candidate["reason"] = "Qualified; outside the six highest-ranked queued setups"
        else:
            order = dict(candidate, id=key, created_date=today, created_at=stamp, status="QUEUED")
            account["orders"].append(order)
            busy.add(candidate["symbol"])
            ids.add(key)
            queued += 1
            candidate["status"] = "QUEUED"
            event(account, today, candidate["symbol"], "QUEUED", "Confirmed setup; not a trade until the next-session opening fill")


class PaperStore:
    def __init__(self, path: Path = DB_PATH, *, selective=False):
        self.path = Path(path)
        self.selective = selective

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=60)
        db.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
        db.commit()
        return db

    def read(self) -> dict:
        with closing(self.connect()) as db:
            row = db.execute("SELECT payload FROM account WHERE id=1").fetchone()
            return json.loads(row[0]) if row else new_account()

    def scan(self, frames: dict[str, list[dict]], members: list[dict], now: datetime, source: str) -> dict:
        now = now.astimezone(IST)
        stamp, today = now.isoformat(), now.date().isoformat()
        benchmark = frames.get("^NSEI", [])
        if not benchmark or (now.date() - date.fromisoformat(benchmark[-1]["date"])).days > 4:
            raise ValueError("Fresh completed Nifty 50 daily data unavailable; portfolio was not changed")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM account WHERE id=1").fetchone()
            account = json.loads(row[0]) if row else new_account()
            account["warnings"] = []
            if self.selective:
                from screener.stock_selective import cancel_unvalidated_orders, policy
                cancel_unvalidated_orders(account, today)
                account["policy"] = policy()
            # A fresh account starts now: no fabricated historical trades.
            if not account["last_session"]:
                account["last_session"] = benchmark[-1]["date"]
                account["equity_history"].append({"date": today, "equity": INITIAL_CAPITAL, "cash": INITIAL_CAPITAL})
            active = any(t["status"] == "OPEN" for t in account["trades"]) or any(o["status"] == "QUEUED" for o in account["orders"])
            if active and account["last_session"] < benchmark[0]["date"]:
                raise ValueError("Account replay exceeds downloaded history; restore missing daily data before continuing")
            advance(account, frames, [b["date"] for b in benchmark])
            candidates = [evaluate_stock(m["symbol"], m.get("name", m["symbol"]), m.get("industry", "Unknown"),
                                         frames.get(m["symbol"], []), benchmark) for m in members]
            if self.selective:
                from screener.stock_selective import design_candidate
                candidates = [design_candidate(candidate) for candidate in candidates]
            candidates.sort(key=lambda c: (-c["score"], c["symbol"]))
            queue_setups(account, candidates, today, stamp)
            scan = {"created_at": stamp, "bar_date": benchmark[-1]["date"], "source": source,
                    "requested": len(members), "available": sum(bool(frames.get(m["symbol"])) for m in members),
                    "stocks": candidates}
            scan_id = db.execute("INSERT INTO scans(created_at,payload) VALUES(?,?)", (stamp, json.dumps(scan, allow_nan=False))).lastrowid
            account.update(last_scan=stamp, latest_scan=dict(scan, id=scan_id), enabled=True)
            db.execute("INSERT OR REPLACE INTO account VALUES(1,?)", (json.dumps(account, allow_nan=False),))
            return self.summary(account, now)

    @staticmethod
    def summary(account: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now(IST)
        month = now.strftime("%Y-%m")
        prior = [p for p in account["equity_history"] if p["date"][:7] < month]
        baseline = prior[-1]["equity"] if prior else INITIAL_CAPITAL
        value = equity(account)
        return dict(account, initial_capital=INITIAL_CAPITAL, equity=value,
                    realized_pnl=sum(t["net_pnl"] for t in account["trades"] if t["status"] != "OPEN"),
                    unrealized_pnl=sum(t["quantity"] * t["mark"] + t["dividends"] - t["cost"] for t in account["trades"] if t["status"] == "OPEN"),
                    weekly_entries=sum(week(t["entry_date"]) == week(now.date().isoformat()) for t in account["trades"]),
                    month=month, month_start_equity=baseline, month_pnl=value-baseline,
                    month_return_pct=(value / baseline - 1) * 100 if baseline else 0,
                    monthly_target=None if account.get("policy", {}).get("monthly_goal_pct", 5) is None else baseline * 0.05,
                    max_weekly=MAX_WEEKLY, max_open=MAX_OPEN)

    def history(self, scan_id: int | None = None) -> list | dict:
        with closing(self.connect()) as db:
            if scan_id is not None:
                row = db.execute("SELECT payload FROM scans WHERE id=?", (scan_id,)).fetchone()
                if not row:
                    raise ValueError("Scan not found")
                return json.loads(row[0])
            return [{"id": row[0], "created_at": row[1]} for row in db.execute("SELECT id,created_at FROM scans ORDER BY id DESC")]
