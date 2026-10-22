"""Timestamped OI research. Never authorizes or blocks a trade."""
from __future__ import annotations

import asyncio
import json
import math
import time


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


class OIShadow:
    """Compare samples at least 60 seconds apart within the same session/contract."""
    def __init__(self):
        self.samples = []
        self.key = None
        self.error = None

    def observe(self, day, contract, quote, received, latency):
        key = (day, contract)
        if key != self.key:
            self.samples = []
            self.key = key
        sample = {"received_at": received, "latency_seconds": latency,
                  "price": number(quote.get("ltp")),
                  "oi": number(quote.get("open_interest")),
                  "volume": number(quote.get("volume"))}
        self.samples.append(sample)
        self.samples = self.samples[-30:]
        self.error = None
        return sample

    def status(self, now, day=None):
        result = {"mode": "shadow", "affects_trades": False,
                  "exchange_oi_timestamp_verified": False,
                  "state": "unavailable", "reason": self.error or "Waiting for OI samples"}
        if not self.samples or (day and self.key[0] != day):
            return result
        last = self.samples[-1]
        result.update(contract=self.key[1], received_at=last["received_at"], samples=len(self.samples))
        if self.error or not 0 <= now-last["received_at"] <= 45 or last["latency_seconds"] > 3:
            return {**result, "reason": self.error or "Delayed or stale quote response"}
        candidates = [s for s in self.samples[:-1] if 60 <= last["received_at"]-s["received_at"] <= 180 and s["latency_seconds"] <= 3]
        if not candidates:
            return {**result, "state": "collecting", "reason": "Need two samples at least 60 seconds apart"}
        first = candidates[-1]
        if any(s[k] is None for s in (first, last) for k in ("price", "oi", "volume")) or first["oi"] <= 0 or last["oi"] <= 0 or first["price"] <= 0 or last["price"] <= 0:
            return {**result, "reason": "Missing OI/price/volume or zero OI baseline"}
        dp, doi, dv = (last[k]-first[k] for k in ("price", "oi", "volume"))
        result.update(price_change=dp, oi_change=doi, volume_change=dv,
                      window_seconds=last["received_at"]-first["received_at"])
        if dv <= 0 or dp == 0 or doi == 0:
            return {**result, "state": "inconclusive", "reason": "No observed price/OI change or no advancing volume"}
        state = ("bullish_participation" if dp > 0 else "bearish_participation") if doi > 0 else ("possible_short_covering" if dp > 0 else "possible_long_unwinding")
        return {**result, "state": state, "reason": "Provisional price/OI context; exchange OI update time unverified"}


async def collect_oi(agent, client):
    """Low-frequency observer; blocking broker calls stay off the feed event loop."""
    next_chain = 0.0
    while agent.state["running"]:
        now = agent._india_now()
        tick = agent.latest_future
        if now.hour*60+now.minute < 555 or now.hour*60+now.minute >= 930 or not tick or time.monotonic()-agent.latest_future_received > 5:
            await asyncio.sleep(5)
            continue
        day = now.date().isoformat()
        token = str(tick.get("instrument_token") or "")
        contract = f"nse_fo|{token}|{tick.get('symbol')}"
        try:
            started = time.monotonic()
            response = await asyncio.to_thread(client.get_quotes, [token], "nse_fo", "all")
            latency = time.monotonic()-started
            received = time.time()
            quotes = response.get("quotes", [])
            quote = next((q for q in quotes if str(q.get("instrument_token")) == token), None)
            if quote is None:
                raise ValueError("Requested futures contract missing from response")
            sample = agent.oi_shadow.observe(day, contract, quote, received, latency)
            agent.journal.record_event(day, "oi_future_snapshot", json.dumps({"contract": contract, **sample, "exchange_oi_timestamp_verified": False}))
            if time.monotonic() >= next_chain:
                next_chain = time.monotonic()+60
                started = time.monotonic()
                chain = await asyncio.to_thread(client.get_option_chain, "BANKNIFTY")
                agent.journal.record_event(day, "oi_chain_snapshot", json.dumps({"received_at": time.time(), "latency_seconds": time.monotonic()-started, "exchange_oi_timestamp_verified": False, "chain": chain}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Avoid persisting authentication-bearing SDK exception strings.
            agent.oi_shadow.error = f"OI collection unavailable ({type(exc).__name__})"
            agent.journal.record_event(day, "oi_collection_error", agent.oi_shadow.error)
        await asyncio.sleep(20)
