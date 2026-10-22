"""Autonomous, simulation-only BANKNIFTY paper-trading agent."""
from __future__ import annotations

import asyncio
import json
import time
from math import isfinite
from datetime import datetime, time as clock_time
from typing import Any
from zoneinfo import ZoneInfo

from backend.paper_trading import PaperTrade
from backend.paper_trend import OpeningTrendEngine, TrendRunnerRules, STRATEGY_VERSION
from backend.paper_journal import PaperJournal
from backend.paper_history import fetch_history
from backend.paper_oi import OIShadow, collect_oi


class PaperAgent:
    strategy_retired = True
    max_trades = 3
    max_losses = 2
    max_daily_loss = 5400.0
    planned_risk_target = 2700.0
    risk_tolerance = 270.0  # 10% execution allowance; shared daily, not per entry.
    cooldown_seconds = 600

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.engine = OpeningTrendEngine(allow_late_start=True)
        self.rules = TrendRunnerRules()
        self.entry_task: asyncio.Task | None = None
        self.position_task: asyncio.Task | None = None
        self.oi_task: asyncio.Task | None = None
        self.oi_shadow = OIShadow()
        self.latest_future: dict[str, Any] | None = None
        self.latest_future_received = 0.0
        self.last_closed_at: datetime | None = None
        self.trade: PaperTrade | None = None
        self.trade_token = ""
        self.last_premium: float | None = None
        self.history: list[dict[str, Any]] = []
        self.journal = PaperJournal()
        self.trade_day = ""
        self.history_contract = ""
        self.state: dict[str, Any] = {"running": False, "signals": [], "trades_today": 0, "losses_today": 0, "max_trades": self.max_trades, "message": "Not started", "future": None}
        saved = self.journal.load_position()
        if saved:
            self.trade = PaperTrade(**saved["trade"])
            self.trade_token = saved["token"]
            self.last_premium = saved.get("last_premium")

    @staticmethod
    def _india_now() -> datetime:
        return datetime.now(ZoneInfo("Asia/Kolkata"))

    @staticmethod
    def _error_text(exc: Exception) -> str:
        """Some legacy SDK exceptions have a broken __str__; never let that stop the worker."""
        try:
            text = str(exc)
            if text:
                return text[:180]
        except Exception:
            pass
        return type(exc).__name__

    def _reset_day(self) -> None:
        today = self._india_now().date().isoformat()
        if today != self.trade_day:
            self.trade_day = today
            self.journal.begin_day(today)
            self.state["pattern_data"] = self.journal.pattern_sample_status()
            self.state["losses_today"] = self.journal.losing_trades_for_day(today)
            self.state["trades_today"] = self.journal.entries_for_day(today)
            self.state["realized_pnl_today"] = self.journal.realized_pnl_for_day(today)
            closed = self.journal.last_close_for_day(today)
            self.last_closed_at = datetime.fromisoformat(closed) if closed else None

    def _snapshot_trade(self, premium: float | None = None) -> dict[str, Any] | None:
        if not self.trade:
            return None
        result = self.trade.as_dict()
        mark = premium if premium is not None else self.trade.exit_price
        if mark is not None:
            result.update(mark_price=mark, points_pnl=round(mark - self.trade.entry_price, 2), unrealized_pnl=round((mark - self.trade.entry_price) * self.trade.quantity, 2))
        return result

    async def _prepare_history(self, client: Any, tick: dict) -> None:
        now = self._india_now()
        token = str(tick.get("instrument_token") or "")
        contract = f"nse_fo|{token}|{tick.get('symbol')}"
        cached = self.journal.cached_candles(contract, now)
        source = "contract cache"
        try:
            fetched = await asyncio.to_thread(fetch_history, client, token, now)
            if fetched:
                self.journal.cache_candles(contract, fetched)
                cached = self.journal.cached_candles(contract, now)
                source = "broker history and contract cache"
            else:
                source += "; broker returned no completed candles"
        except Exception as exc:
            # Do not expose request exceptions that may contain authentication data.
            source += f"; broker history unavailable ({type(exc).__name__})"
        # Keep the most recent prior session and today's bars, never mix contracts.
        prior_days = [self.engine._candle_day(c) for c in cached if self.engine._candle_day(c) < self.trade_day]
        previous = max(prior_days, default="")
        prior = [c for c in cached if self.engine._candle_day(c) == previous][-80:]
        today = [c for c in cached if self.engine._candle_day(c) == self.trade_day]
        self.engine = OpeningTrendEngine(allow_late_start=True)
        self.engine.session_day = self.trade_day
        self.engine.load_completed_candles(prior + today, allow_initial_signal=self.state["trades_today"] == 0 and self.trade is None)
        self.history_contract = contract
        self.state["warmup_history"] = f"Loaded {len(prior)} prior-session and {len(today)} today candles from {source}"
        self.journal.record_event(self.trade_day, "history_warmup", self.state["warmup_history"])

    async def _select_option(self, client: Any, direction: str, underlying_price: float | None = None) -> dict[str, Any]:
        """Choose the second available in-the-money strike using the signal's future price."""
        chain = await asyncio.to_thread(client.get_option_chain, "BANKNIFTY")
        is_long = direction == "long"
        token_key, symbol_key, ask_key, ltp_key, lot_key = ("call_token", "call_symbol", "call_ask", "call_ltp", "call_lot_size") if is_long else ("put_token", "put_symbol", "put_ask", "put_ltp", "put_lot_size")
        rows = sorted((row for row in chain.get("chains", []) if row.get(token_key) and row.get(symbol_key)), key=lambda row: float(row["strike_price"]))
        spot = float(underlying_price or chain.get("ltp") or 0)
        if not rows or spot <= 0:
            raise RuntimeError(chain.get("message") or f"BANKNIFTY option chain has no live {'CE' if is_long else 'PE'} contracts.")
        atm = min(range(len(rows)), key=lambda index: abs(float(rows[index]["strike_price"]) - spot))
        target_index = atm - 2 if is_long else atm + 2
        if target_index < 0 or target_index >= len(rows):
            raise RuntimeError(f"Not enough live {'ITM CE' if is_long else 'ITM PE'} strikes around the BANKNIFTY future price.")
        option = rows[target_index]
        strike = float(option["strike_price"])
        if (is_long and strike >= spot) or (not is_long and strike <= spot):
            raise RuntimeError(f"Selected BANKNIFTY {'CE' if is_long else 'PE'} strike {strike:g} is not in the money at future price {spot:.2f}.")
        # Refresh the selected contract, rather than using an aging chain price.
        started = time.monotonic()
        quotes = await asyncio.to_thread(client.get_quotes, [str(option[token_key])], "nse_fo", "all")
        quote = (quotes.get("quotes") or [{}])[0]
        premium, bid = float(quote.get("ask") or 0), float(quote.get("bid") or 0)
        if time.monotonic()-started > 3 or not all(isfinite(v) and v > 0 for v in (premium,bid)):
            raise RuntimeError("Entry skipped: fresh two-sided option quote unavailable")
        if bid > premium or premium-bid > min(3.0, premium*0.005):
            raise RuntimeError("Entry skipped: option spread exceeds paper execution limit")
        itm_distance = spot - strike if is_long else strike - spot
        return {
            "symbol": str(option[symbol_key]),
            "token": str(option[token_key]),
            "lot_size": int(option.get(lot_key) or 0),
            "premium": premium,
            "strike": strike,
            "underlying_price": spot,
            "itm_distance": itm_distance,
            "bid": bid,
            "quote_latency_seconds": time.monotonic()-started,
        }

    async def _option_premium(self, client: Any) -> float | None:
        quotes = await asyncio.to_thread(client.get_quotes, [self.trade_token], "nse_fo", "all")
        quote = (quotes.get("quotes") or [{}])[0]
        value = quote.get("bid")
        return float(value) if value not in (None, "", 0) else None

    def _close_trade(self, premium: float, message: str) -> None:
        closed = self._snapshot_trade(premium) or {}
        self.history.insert(0, closed)
        self.journal.close_trade(closed,clear_position=True)
        self.state["losses_today"] = self.journal.losing_trades_for_day(self.trade_day)
        self.state["realized_pnl_today"] = self.journal.realized_pnl_for_day(self.trade_day)
        self.last_closed_at = self._india_now()
        self.journal.record_event(self.trade_day, "trade_closed", message, self.trade.direction if self.trade else None, premium)
        self.history = self.history[:50]
        self.trade, self.trade_token, self.last_premium, self.state["message"] = None, "", None, message

    def _entry_block(self) -> str | None:
        now = self._india_now()
        if not self.state["running"] or not clock_time(9, 18) <= now.time() < clock_time(15, 0):
            return "Outside entry window (09:18–15:00 IST)"
        if self.trade:
            return "A paper position is already open"
        if self.state["trades_today"] >= self.max_trades:
            return "Daily trade limit reached"
        if self.state["losses_today"] >= self.max_losses or self.state.get("realized_pnl_today",0) <= -self.max_daily_loss:
            return "Daily loss lockout"
        if self.last_closed_at and (now-self.last_closed_at).total_seconds() < self.cooldown_seconds:
            return "Ten-minute post-exit cooldown"
        if time.monotonic()-self.latest_future_received > 5:
            return "Futures feed is stale"
        return None

    def _save_position(self):
        self.journal.save_position({"trade": self.trade.as_dict(), "token": self.trade_token, "last_premium": self.last_premium,"strategy":STRATEGY_VERSION} if self.trade else None)

    async def _enter(self, client, signal, signal_price):
        try:
            started = time.monotonic()
            blocked = self._entry_block()
            if blocked:
                raise RuntimeError(blocked)
            selected = await self._select_option(client, signal, signal_price)
            blocked = self._entry_block()
            if blocked:
                raise RuntimeError(blocked)
            if time.monotonic()-started > 20:
                raise RuntimeError("Signal expired during option lookup")
            expected = "bullish" if signal == "long" else "bearish"
            if self.engine._market_regime() != expected:
                raise RuntimeError("Trend no longer confirmed after option lookup")
            current = float(self.latest_future["ltp"])
            if abs(current-signal_price) > 15:
                raise RuntimeError("Future moved more than 15 points during lookup")
            lot = selected["lot_size"]
            quantity = self.rules.quantity_for_lot_size(lot)
            remaining = self.max_daily_loss + min(0,self.state.get("realized_pnl_today",0))
            planned_risk = quantity*self.rules.stop_loss_points
            allowed_risk = min(self.planned_risk_target+self.risk_tolerance, remaining+self.risk_tolerance)
            if planned_risk > allowed_risk:
                raise RuntimeError("Contract quantity exceeds remaining planned risk budget")
            trade = self.rules.open_trade(selected["symbol"], quantity, selected["premium"], signal)
            details = json.dumps({"strategy":STRATEGY_VERSION,**selected,"signal_price":signal_price,"entry_future":current,"latency_seconds":round(time.monotonic()-started,3),"planned_risk":planned_risk,"allowed_risk":allowed_risk,"risk_tolerance":self.risk_tolerance,"tolerance_used":max(0,planned_risk-min(self.planned_risk_target,remaining))})
            self.journal.open_position(trade.as_dict(),selected["token"],self.trade_day,details,STRATEGY_VERSION)
            self.trade = trade
            self.trade_token, self.last_premium = selected["token"], selected["premium"]
            self.state["trades_today"] += 1
            self.state["message"] = f"Virtual BUY {selected['symbol']} at {selected['premium']:.2f}"
        except Exception as exc:
            detail = self._error_text(exc)
            self.state["message"] = f"Paper entry skipped: {detail}"
            self.journal.record_event(self.trade_day,"entry_skipped",detail,signal,signal_price)

    async def _manage_position(self, client):
        """Independent polling: a slow broker response never stalls futures ingestion."""
        while self.state["running"]:
            trade = self.trade
            if trade:
                try:
                    started = time.monotonic()
                    premium = await self._option_premium(client)
                    latency = time.monotonic()-started
                    if self.trade is not trade:
                        continue
                    if premium is None or not isfinite(premium) or premium <= 0 or latency > 3:
                        raise RuntimeError("Fresh executable bid unavailable; exit monitoring degraded")
                    self.last_premium = premium
                    old_stop = trade.stop_price
                    self.rules.on_tick(trade, premium)
                    now = self._india_now()
                    if now.time() >= clock_time(15,20) or datetime.fromisoformat(trade.opened_at).astimezone(ZoneInfo("Asia/Kolkata")).date() < now.date():
                        self.rules.close_for_market_close(trade,premium)
                    self.journal.record_event(self.trade_day,"option_mark",json.dumps({"strategy":STRATEGY_VERSION,"bid":premium,"latency_seconds":round(latency,3),"stop":trade.stop_price,"peak":trade.peak_premium}),trade.direction,premium)
                    if old_stop != trade.stop_price:
                        self.journal.record_event(self.trade_day,"stop_updated",f"{old_stop:.2f} -> {trade.stop_price:.2f}",trade.direction,premium)
                    if trade.status == "closed":
                        self._close_trade(premium,f"Paper trade closed: {trade.exit_reason}")
                    else:
                        self._save_position()
                except Exception as exc:
                    detail = self._error_text(exc)
                    self.state["message"] = f"Option monitoring degraded: {detail}"
                    self.journal.record_event(self.trade_day,"option_quote_error",detail,trade.direction,self.last_premium)
            await asyncio.sleep(2)

    async def _run(self, client: Any) -> None:
        try:
            self.position_task = asyncio.create_task(self._manage_position(client))
            self.oi_task = asyncio.create_task(collect_oi(self, client))
            while self.state["running"] and self._india_now().time() < clock_time(9,15):
                self.state["message"] = "Armed; waiting for 09:15 IST live session"
                await asyncio.sleep(1)
            async for tick in client.banknifty_future_tick_stream():
                if not self.state["running"]:
                    break
                if tick.get("type") == "status":
                    self.state["message"] = tick.get("message") or tick.get("state", "")
                    self.state["future"] = tick.get("symbol") or self.state.get("future")
                    if self.trade_day:
                        self.journal.record_event(self.trade_day, "feed_status", self.state["message"])
                    continue
                if tick.get("type") != "quote":
                    continue
                now = self._india_now()
                stamp = tick.get("last_trade_time")
                if not isinstance(stamp, (float,int)):
                    continue
                if stamp > 10_000_000_000:
                    stamp /= 1000
                tick_time = datetime.fromtimestamp(stamp, ZoneInfo("Asia/Kolkata"))
                if tick_time.date() != now.date() or not clock_time(9,15) <= tick_time.time() < clock_time(15,30):
                    continue
                age = now.timestamp()-stamp
                if age < -2 or age > 5:
                    self.state["message"] = "Stale futures tick ignored"
                    continue
                self.latest_future, self.latest_future_received = tick, time.monotonic()
                previous_day = self.trade_day
                self._reset_day()
                contract = f"nse_fo|{tick.get('instrument_token') or ''}|{tick.get('symbol')}"
                if not self.history_contract or contract != self.history_contract or previous_day != self.trade_day:
                    await self._prepare_history(client, tick)
                self.state["future"] = tick.get("symbol")
                signal = self.engine.on_tick(float(tick["ltp"]), int(tick["volume"]), float(stamp))
                if self.engine.last_completed_candle:
                    completed = self.engine.last_completed_candle
                    self.journal.record_candle(self.trade_day, completed)
                    self.journal.cache_candles(self.history_contract, [completed])
                    self.journal.record_pattern_sample(
                        self.trade_day,
                        completed.minute,
                        self.engine.latest_pattern_features(),
                    )
                    if len(self.engine.candles) >= 2:
                        target_minute = completed.minute - 5
                        sample = next((candle for candle in reversed(self.engine.candles[:-1]) if candle.minute == target_minute), None)
                        if sample:
                            outcome = completed.close - sample.close
                            self.journal.label_pattern_sample(self.trade_day, sample.minute, round(outcome, 2))
                    self.state["pattern_data"] = self.journal.pattern_sample_status()
                    self.engine.last_completed_candle = None
                if signal:
                    oi_context = self.oi_shadow.status(time.time(), self.trade_day)
                    self.journal.record_event(self.trade_day, "oi_signal_shadow", json.dumps(oi_context), signal, float(tick["ltp"]))
                    signal_detail = f"{STRATEGY_VERSION}: {signal} signal confirmed by BANKNIFTY {self.engine.diagnostics()['market_regime']} regime"
                    self.journal.record_event(self.trade_day, "signal", signal_detail, signal, float(tick["ltp"]))
                    self.state["signals"] = [{"at": tick["updated_at"], "ltp": tick["ltp"], "state": signal_detail}, *self.state["signals"]][:30]
                    if not self.trade and (self.entry_task is None or self.entry_task.done()):
                        self.entry_task = asyncio.create_task(self._enter(client,signal,float(tick["ltp"])))
        except asyncio.CancelledError:
            self.state["running"] = False
            raise
        except Exception as exc:
            detail = self._error_text(exc)
            self.state["message"] = f"Paper feed stopped: {detail}"
            if self.trade_day:
                self.journal.record_event(self.trade_day, "feed_error", detail)
        finally:
            # Losing the futures stream must not abandon an already open position.
            try:
                while self.state["running"] and self.trade and self.position_task and not self.position_task.done():
                    self.state["message"] = "Futures feed ended; monitoring the existing paper position only"
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            self.state["running"] = False
            pending = [t for t in (self.entry_task,self.position_task,self.oi_task) if t is not None]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
            self.entry_task = self.position_task = None
            self.oi_task = None
            self.task = None

    def start(self, client: Any) -> dict:
        if self.strategy_retired:
            self.state["message"] = "Strategy retired at user request. Historical results remain available."
            return self.status()
        if self.task and not self.task.done():
            return self.status()
        self._reset_day()
        self.engine = OpeningTrendEngine(allow_late_start=True)
        self.engine.session_day = self.trade_day
        self.history_contract = ""
        self.oi_shadow = OIShadow()
        self.task = asyncio.create_task(self._run(client))
        message = "Connecting to BANKNIFTY futures; historical warm-up will load for the live contract"
        self.state.update(running=True, signals=[], future=None, message=message, warmup_history="Waiting for the live contract")
        self.journal.record_event(self.trade_day,"strategy_started",json.dumps({"strategy":STRATEGY_VERSION,"max_trades":self.max_trades,"max_losses":self.max_losses,"daily_loss_limit":self.max_daily_loss,"cooldown_seconds":self.cooldown_seconds}))
        return self.status()

    def stop(self) -> dict:
        self.state["running"] = False
        if self.task:
            self.task.cancel()
        self.state["message"] = "Stopped. Any paper position remains virtual only."
        return self.status()

    def status(self) -> dict:
        self._reset_day()
        result = dict(self.state)
        result.update(
            open_trade=self._snapshot_trade(self.last_premium),
            history=self.history,
            losses_today=self.state.get("losses_today", 0),
            signal_diagnostics=self.engine.diagnostics(),
            pattern_data=self.state.get("pattern_data") or self.journal.pattern_sample_status(),
            mode="paper",
            strategy_version=STRATEGY_VERSION,
            strategy_retired=self.strategy_retired,
            oi_shadow=self.oi_shadow.status(time.time(), self.trade_day),
            planned_risk_policy={"target":self.planned_risk_target,"tolerance":self.risk_tolerance,"maximum_per_trade":self.planned_risk_target+self.risk_tolerance,"maximum_projected_daily_loss":self.max_daily_loss+self.risk_tolerance},
            risk_controls={"max_losses":self.max_losses,"daily_loss_limit":self.max_daily_loss,"cooldown_minutes":self.cooldown_seconds//60,"stop_points":30,"trail":"+30 protects +5; from +60 trail 30 behind peak","target":"runner; no fixed target"},
        )
        return result


agent = PaperAgent()
