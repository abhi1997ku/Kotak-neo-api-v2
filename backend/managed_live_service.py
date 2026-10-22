"""Price ticks wake a serialized real-order manager; REST verifies filled quantities."""
import asyncio
import time
from contextlib import contextmanager
from collections import deque
import math
from pathlib import Path

from backend.managed_broker import ManagedBroker
from backend.managed_live import LiveManager, DONE
from backend.session_manager import get_session_manager


class LiveService:
    def __init__(self, manager):
        self.manager = manager
        self.mutex = asyncio.Lock()
        self.wake = asyncio.Event()
        self.quote = None
        self.quote_key = None
        self.price_state = "idle"
        self.order_state = "idle"
        self.error = ""
        self.tasks = []
        self.client = None
        self.last_reconcile = 0.0
        self.tick_queue = deque()
        self.overflow = False
        self.next_connect = 0.0

    def broker(self):
        client = get_session_manager().get_client()
        if not client:
            raise ValueError("Login required. Local triggers cannot execute without a session.")
        return ManagedBroker(client)

    async def stop_feeds(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        self.quote = None
        self.tick_queue.clear()
        self.price_state = self.order_state = "idle"

    async def prices(self, client, key):
        self.price_state = "connecting"
        try:
            async for event in client.option_chain_tick_stream([{"exchange_segment": key[0], "instrument_token": key[1]}]):
                if get_session_manager().get_client() is not client:
                    return
                if event.get("type") == "status":
                    self.price_state = event.get("state", "unknown")
                elif event.get("type") == "quote" and (str(event.get("exchange_segment")).lower(), str(event.get("instrument_token"))) == key:
                    price = float(event.get("ltp") or 0)
                    if not math.isfinite(price) or price <= 0:
                        continue
                    self.quote = {"ltp": price, "received_at": time.time()}
                    if len(self.tick_queue) >= 1024:
                        self.overflow = True
                    else:
                        self.tick_queue.append(self.quote)
                    self.price_state = "connected"
                    self.wake.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self.price_state = "disconnected"
            self.quote = None

    async def orders(self, broker):
        self.order_state = "connecting"
        try:
            async for event in broker.order_events():
                self.order_state = "connected"
                self.wake.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self.order_state = "disconnected"

    async def ensure_feeds(self, broker, key):
        if self.client is broker.client and self.quote_key == key and self.tasks and not any(t.done() for t in self.tasks):
            return
        if self.client is broker.client and self.quote_key == key and time.monotonic() < self.next_connect:
            return
        await self.stop_feeds()
        self.client, self.quote_key = broker.client, key
        self.next_connect = time.monotonic() + 2
        self.tasks = [asyncio.create_task(self.prices(broker.client, key)), asyncio.create_task(self.orders(broker))]
        self.last_reconcile = 0

    async def create(self, request):
        async with self.mutex:
            broker = self.broker()
            existing = [r for r in await asyncio.to_thread(self.manager.list, broker.account) if r["request_id"] == request.request_id]
            if existing:
                if existing[0]["request"] != request.model_dump():
                    raise ValueError("Request ID already used with different values.")
                return existing[0]
            if await asyncio.to_thread(self.manager.active, broker.account):
                raise ValueError("One active managed trade is supported. Finish or release it before another entry.")
            await self.ensure_feeds(broker, (request.exchange_segment, request.instrument_token))
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if self.order_state == "connected" and self.quote and time.time() - self.quote["received_at"] < 2:
                    break
                await asyncio.sleep(0.05)
            else:
                await self.stop_feeds()
                raise ValueError("Fresh option prices and order feed are required before a managed entry. No entry submitted.")
            result = await asyncio.to_thread(self.manager.create, request, broker)
            self.wake.set()
            return result

    async def action(self, trade_id, action):
        async with self.mutex:
            result = await asyncio.to_thread(self.manager.action, trade_id, action, self.broker())
            self.wake.set()
            return result

    async def snapshot(self):
        broker = self.broker()
        rows = await asyncio.to_thread(self.manager.list, broker.account)
        quote = self.quote
        return {"mode": "live", "live_execution_enabled": True, "feed_state": self.price_state,
                "order_feed_state": self.order_state, "error": self.error,
                "trades": [{**{k: v for k, v in r.items() if k not in {"account", "request", "baseline_ids"}},
                            "ltp": quote["ltp"] if quote and r["state"] not in DONE else None,
                            "tick_age_seconds": round(time.time() - quote["received_at"], 1) if quote and r["state"] not in DONE else None}
                           for r in rows]}

    async def run(self):
        try:
            while True:
                try:
                    async with self.mutex:
                        client = get_session_manager().get_client()
                        if not client:
                            await self.stop_feeds()
                            self.error = "Login required; local triggers are inactive."
                        else:
                            broker = ManagedBroker(client)
                            rows = await asyncio.to_thread(self.manager.active, broker.account)
                            if not rows:
                                await self.stop_feeds()
                                self.error = ""
                            else:
                                row = rows[0]
                                await self.ensure_feeds(broker, (row["exchange_segment"], row["instrument_token"]))
                                if self.overflow:
                                    await asyncio.to_thread(self.manager.save, row, state="RECOVERY", note="Price processing fell behind. Reconcile before resuming local triggers.")
                                    self.tick_queue.clear()
                                    self.overflow = False
                                if time.monotonic() - self.last_reconcile >= 1:
                                    await asyncio.to_thread(self.manager.reconcile, broker)
                                    self.last_reconcile = time.monotonic()
                                    self.error = ""
                                if self.price_state == "connected" and self.order_state == "connected":
                                    for _ in range(min(128, len(self.tick_queue))):
                                        quote = self.tick_queue.popleft()
                                        await asyncio.to_thread(self.manager.tick, broker, quote["ltp"], quote["received_at"])
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.error = f"Management waiting: {str(exc)[:180]}"
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.stop_feeds()


_service = None


def get_live_service():
    global _service
    if _service is None:
        path = Path(__file__).resolve().parent / "data"
        path.mkdir(exist_ok=True)
        _service = LiveService(LiveManager(path / "managed_live.sqlite3"))
    return _service


@contextmanager
def manual_order_guard(client, *, symbol=None, order_id=None):
    """Serialize regular terminal orders with managed orders in the live process."""
    if _service is None:
        yield
        return
    broker = ManagedBroker(client)
    with _service.manager.lock:
        for row in _service.manager.active(broker.account):
            if (symbol and symbol == row["symbol"]) or (order_id and order_id in {row["entry_id"], row["exit_id"]}):
                raise ValueError("This instrument/order is managed. Use Managed Trades or release management before a separate order or modification.")
        yield
