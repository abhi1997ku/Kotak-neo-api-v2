"""Read-only underlying tick receiver, independent of REST request latency."""
import asyncio
import math
import threading
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
        threading.Thread(target=lambda: asyncio.run(self.run()), daemon=True).start()

    async def run(self) -> None:
        while True:
            try:
                async for event in client.stream([self.code.replace('_', ':')], mode='ltp'):
                    raw = event.get('raw', {})
                    if str(raw.get('instrument')) not in (self.code.replace('_', ':'), self.code.split('_')[1]):
                        continue
                    price = float(raw.get('data', {}).get('ltp', 0))
                    if not math.isfinite(price) or price <= 0:
                        continue
                    tick = {'price': price, 'received_at': event['received_at']}
                    with self.lock:
                        self.connected = True
                        self.overflow |= len(self.pending) == self.pending.maxlen
                        self.latest = tick
                        self.pending.append(tick)
            except Exception:
                with self.lock:
                    self.connected = False
            await asyncio.sleep(2)

    def drain(self) -> tuple[list[dict], dict | None, bool]:
        with self.lock:
            ticks = list(self.pending)
            self.pending.clear()
            fresh = (self.connected and self.latest is not None and not self.overflow
                     and 0 <= (pd.Timestamp.now(tz='UTC') - pd.Timestamp(self.latest['received_at'])).total_seconds() <= 3)
            self.overflow = False
            return ticks, self.latest, fresh
