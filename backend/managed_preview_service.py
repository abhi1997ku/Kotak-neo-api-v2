"""Backend-owned live price subscriptions for simulated managed trades only."""
import asyncio
import math
import time
from pathlib import Path

from backend.managed_trades import PreviewStore, TERMINAL
from backend.session_manager import get_session_manager


class PreviewService:
    def __init__(self, store):
        self.store = store
        self.quotes = {}
        self.feed_state = "idle"

    def snapshot(self):
        now = time.time()
        quotes = dict(self.quotes)
        return {
            "mode": "preview", "live_execution_enabled": False, "feed_state": self.feed_state,
            "trades": [{**row, **quotes.get((row["exchange_segment"], row["instrument_token"]), {}),
                        "tick_age_seconds": round(now - quotes[(row["exchange_segment"], row["instrument_token"])]["received_at"], 1)
                        if (row["exchange_segment"], row["instrument_token"]) in quotes else None}
                       for row in self.store.list()],
        }

    async def consume(self, client, tokens):
        self.feed_state = "connecting"
        try:
            async for event in client.option_chain_tick_stream(tokens):
                if event.get("type") == "status":
                    self.feed_state = event.get("state", "unknown")
                if event.get("type") != "quote":
                    continue
                if get_session_manager().get_client() is not client:
                    return
                received = time.time()
                try:
                    price = float(event.get("ltp") or 0)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(price) or price <= 0:
                    continue
                key = (str(event["exchange_segment"]).lower(), str(event["instrument_token"]))
                self.quotes[key] = {"ltp": price, "received_at": received}
                # Offload SQLite writes so a preview never blocks the live event loop.
                await asyncio.to_thread(self.store.tick, *key, price, received_at=received)
            self.feed_state = "disconnected"
        except asyncio.CancelledError:
            raise
        except Exception:
            self.feed_state = "disconnected"

    async def run(self):
        task = None
        previous = None
        retry_after = 0.0
        try:
            while True:
                rows = await asyncio.to_thread(self.store.list)
                keys = sorted({(r["exchange_segment"], r["instrument_token"]) for r in rows if r["state"] not in TERMINAL | {"PAUSED"}})
                client = get_session_manager().get_client()
                signature = (id(client), tuple(keys))
                if task and (signature != previous or task.done()):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    task = None
                    self.quotes.clear()
                    retry_after = time.monotonic() + (2 if signature == previous else 0)
                if not client:
                    self.feed_state = "login_required"
                elif not keys:
                    self.feed_state = "idle"
                elif task is None and time.monotonic() >= retry_after:
                    task = asyncio.create_task(self.consume(client, [{"exchange_segment": s, "instrument_token": t} for s, t in keys]))
                previous = signature
                await asyncio.sleep(0.5)
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.feed_state = "stopped"


_service = None


def get_preview_service():
    global _service
    if _service is None:
        directory = Path(__file__).resolve().parent / "data"
        directory.mkdir(exist_ok=True)
        _service = PreviewService(PreviewStore(directory / "managed_preview.sqlite3"))
    return _service
