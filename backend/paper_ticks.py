"""Read-only underlying tick receiver, independent of REST request latency."""
import asyncio
import math
import threading
import time
from contextlib import aclosing
from collections import deque

import pandas as pd
from backend.indstocks_data import client


class PaperTicks:
    """Buffer ticks for exit crossings; never substitute an old tick for a fresh one."""

    def __init__(self, code: str):
        self.code = code
        self.lock = threading.Lock()
        self.pending = deque(maxlen=10000)
        self.latest = None
        self.connected = False
        self.overflow = False
        self.attempts = 0
        self.last_error = None
        self.stopped = threading.Event()
        self.loop = None
        self.task = None
        threading.Thread(target=lambda: asyncio.run(self.run()), daemon=True).start()

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        while not self.stopped.is_set():
            with self.lock:
                self.attempts += 1
            try:
                async with aclosing(client.stream([self.code.replace('_', ':')], mode='ltp')) as stream:
                    deadline = time.monotonic() + 10
                    while True:
                        event = await asyncio.wait_for(anext(stream), timeout=max(0.01, deadline-time.monotonic()))
                        raw = event.get('raw', {})
                        if str(raw.get('instrument')) not in (self.code.replace('_', ':'), self.code.split('_')[1]):
                            continue
                        price = float(raw.get('data', {}).get('ltp', 0))
                        if not math.isfinite(price) or price <= 0:
                            continue
                        deadline = time.monotonic() + 10
                        tick = {'price': price, 'received_at': event['received_at']}
                        with self.lock:
                            self.connected = True
                            self.last_error = None
                            self.overflow |= len(self.pending) == self.pending.maxlen
                            self.latest = tick
                            self.pending.append(tick)
            except asyncio.CancelledError:
                # Normal shutdown on token rotation/session end is not a feed error.
                with self.lock:
                    self.connected = False
                return
            except Exception as exc:
                with self.lock:
                    self.connected = False
                    self.last_error = 'NO_USABLE_TICKS_TIMEOUT' if isinstance(exc, TimeoutError) else type(exc).__name__
            await asyncio.sleep(2)

    def stop(self) -> None:
        """Close the subscription on session end or contract rollover."""
        self.stopped.set()
        with self.lock:
            self.connected = False
        if self.loop and self.task and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.task.cancel)

    def health(self) -> dict:
        """Expose sanitized diagnostics, never provider credentials or bodies."""
        with self.lock:
            return {'attempts': self.attempts, 'connected': self.connected,
                    'last_error': self.last_error, 'overflow': self.overflow}

    def drain(self) -> tuple[list[dict], dict | None, bool]:
        with self.lock:
            ticks = list(self.pending)
            self.pending.clear()
            fresh = (self.connected and self.latest is not None and not self.overflow
                     and 0 <= (pd.Timestamp.now(tz='UTC') - pd.Timestamp(self.latest['received_at'])).total_seconds() <= 3)
            self.overflow = False
            return ticks, self.latest, fresh
