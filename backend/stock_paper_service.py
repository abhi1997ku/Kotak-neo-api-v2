"""Daily stock paper research worker; no broker session dependency."""
import asyncio
import logging
import math
import threading
from datetime import datetime, timedelta

import pandas as pd

from backend.watchlist_catalog import get_index_constituents, constituents_source
from screener.data_fetcher import yf
from screener.stock_paper import IST, PaperStore
from screener.swing_store import SwingStore

logger = logging.getLogger(__name__)


def completed_bars(frame, now):
    if frame is None or frame.empty:
        return []
    rows = {}
    for stamp, row in frame.iterrows():
        stamp = pd.Timestamp(stamp)
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert(IST)
        day = stamp.date()
        if day > now.date() or (day == now.date() and (now.hour, now.minute) < (16, 10)):
            continue
        values = {str(key).lower(): value for key, value in row.items()}
        try:
            bar = {k: float(values[k]) for k in ("open", "high", "low", "close", "volume")}
            bar["split"] = float(values.get("stock splits", 0) or 0)
            bar["dividend"] = float(values.get("dividends", 0) or 0)
            if not all(math.isfinite(v) for v in bar.values()):
                continue
            if min(bar[k] for k in ("open", "high", "low", "close")) <= 0 or bar["volume"] < 0:
                continue
            if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
                continue
            bar["date"] = day.isoformat()
            rows[bar["date"]] = bar
        except (KeyError, ValueError, TypeError):
            continue
    return [rows[day] for day in sorted(rows)]


def download(symbols, now, history_days=730):
    if yf is None:
        raise ValueError("Install yfinance to load real daily prices. No demo prices are used.")
    frames = {}
    for offset in range(0, len(symbols), 50):
        batch = {s: s if s.startswith("^") else f"{s}.NS" for s in symbols[offset:offset + 50]}
        raw = yf.download(list(batch.values()), start=(now - timedelta(days=history_days)).date().isoformat(),
                          end=(now + timedelta(days=1)).date().isoformat(), interval="1d",
                          group_by="ticker", auto_adjust=False, actions=True,
                          progress=False, threads=8, timeout=25)
        for symbol, ticker in batch.items():
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    frame = raw[ticker] if ticker in raw.columns.get_level_values(0) else raw.xs(ticker, axis=1, level=1)
                else:
                    frame = raw
                frames[symbol] = completed_bars(frame, now)
            except (KeyError, AttributeError):
                frames[symbol] = []
    return frames


class StockPaperService:
    def __init__(self):
        self.store = SwingStore()
        self.lock = threading.Lock()
        self.running = False
        self.error = None
        self.last_attempt = None

    def status(self):
        return dict(self.store.summary(self.store.read()), running=self.running,
                    error=self.error, last_attempt=self.last_attempt)

    def start(self):
        if not self.lock.acquire(blocking=False):
            return {"running": True}
        self.running, self.error = True, None
        self.last_attempt = datetime.now(IST).isoformat()
        threading.Thread(target=self._run, name="stock-paper-scan", daemon=True).start()
        return {"running": True}

    def _run(self):
        try:
            now = datetime.now(IST)
            from screener.swing_data import refresh
            account = self.store.read()
            symbols = set()
            symbols.update(t["symbol"] for t in account["trades"] if t["status"] == "OPEN")
            symbols.update(o["symbol"] for o in account["orders"] if o["status"] == "QUEUED")
            data = refresh(now, symbols)
            self.store.scan(data["frames"], data["members"], now, data["universe_source"])
        except Exception as exc:
            logger.exception("Stock paper scan failed")
            self.error = str(exc)
        finally:
            self.running = False
            self.lock.release()

    async def daily_loop(self):
        # First manual scan enables this local worker; no external automation.
        while True:
            try:
                now = datetime.now(IST)
                account = await asyncio.to_thread(self.store.read)
                due = now.weekday() < 5 and (now.hour, now.minute) >= (16, 10)
                attempt = datetime.fromisoformat(self.last_attempt) if self.last_attempt else None
                attempted_today = attempt and attempt.date() == now.date() and (attempt.hour, attempt.minute) >= (16, 10)
                scanned_today = account["latest_scan"] and account["latest_scan"]["bar_date"] == now.date().isoformat()
                if account["enabled"] and due and not attempted_today and not scanned_today:
                    self.start()
            except Exception:
                logger.exception("Stock paper daily worker failed")
            await asyncio.sleep(60)


service = StockPaperService()
