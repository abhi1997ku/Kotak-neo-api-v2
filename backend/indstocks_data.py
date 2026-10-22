"""Read-only INDstocks transport for paper research; no trading endpoints."""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import AsyncIterator

import requests
import websockets

from config import get_env
from pathlib import Path
from dotenv import dotenv_values
import os

_TOKEN_FILE = Path(__file__).resolve().parents[1] / ".env"
_TOKEN_MTIME = _TOKEN_FILE.stat().st_mtime_ns if _TOKEN_FILE.exists() else None


class INDstocksError(RuntimeError):
    """Sanitized provider error, safe to return without credentials."""


def decode_stream_event(raw: str | bytes) -> dict:
    """Decode either documented JSON objects or observed double-encoded frames."""
    event = json.loads(raw)
    if isinstance(event, str):
        event = json.loads(event)
    if not isinstance(event, dict):
        raise INDstocksError("Invalid INDstocks stream event")
    return event


def best_bid_ask(code: str, quote: dict) -> dict:
    """Read observed nested depth without substituting LTP for execution prices."""
    import math
    depth = quote.get("market_depth", {})
    depth = depth.get(code, depth)
    levels = depth.get("depth", [])
    try:
        bid = float(str(levels[0]["buy"]["price"]).replace(",", ""))
        ask = float(str(levels[0]["sell"]["price"]).replace(",", ""))
        if not all(math.isfinite(x) and x > 0 for x in (bid, ask)) or bid > ask:
            raise ValueError("Invalid spread")
    except (IndexError, KeyError, TypeError, ValueError):
        return {"bid": None, "ask": None, "depth_valid": False, "timestamp_verified": False}
    return {"bid": bid, "ask": ask, "depth_valid": True, "timestamp_verified": False}


class INDstocksData:
    """Only allow documented read-only paths on the fixed provider host."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request = 0.0

    @staticmethod
    def token() -> str:
        global _TOKEN_MTIME
        modified = _TOKEN_FILE.stat().st_mtime_ns if _TOKEN_FILE.exists() else None
        if modified != _TOKEN_MTIME:
            updated = dotenv_values(_TOKEN_FILE).get("INDSTOCKS_ACCESS_TOKEN") if modified is not None else None
            if updated:
                os.environ["INDSTOCKS_ACCESS_TOKEN"] = updated
            else:
                os.environ.pop("INDSTOCKS_ACCESS_TOKEN", None)
            _TOKEN_MTIME = modified
        token = get_env("INDSTOCKS_ACCESS_TOKEN")
        if not token:
            raise INDstocksError("Set INDSTOCKS_ACCESS_TOKEN in the local .env and restart the paper backend.")
        return token

    def _get(self, path: str, params: dict | None = None) -> dict:
        allowed = {"/user/profile", "/market/quotes/full"}
        if path not in allowed and not re.fullmatch(r"/market/historical/(expired/)?(1minute|5minute)", path):
            raise ValueError("Endpoint is not allowed by the data-only connector")
        token = self.token()
        with self._lock:
            time.sleep(max(0, 0.25 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            try:
                response = requests.get(
                    "https://api.indstocks.com" + path,
                    headers={"Authorization": token}, params=params,
                    timeout=(5, 15), allow_redirects=False,
                )
            except requests.RequestException:
                raise INDstocksError("INDstocks network request failed or timed out") from None
        if response.status_code in (401, 403):
            raise INDstocksError("INDstocks token rejected or expired; generate a fresh token")
        if response.status_code == 429:
            raise INDstocksError("INDstocks rate limit reached; retry later")
        if not 200 <= response.status_code < 300:
            raise INDstocksError(f"INDstocks returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise INDstocksError("INDstocks returned invalid JSON") from None
        if not isinstance(payload, dict) or not (
            payload.get("status") == "success" or payload.get("success") is True
        ) or payload.get("success") is False:
            raise INDstocksError("INDstocks reported an unsuccessful response")
        if not isinstance(payload.get("data"), dict):
            raise INDstocksError("INDstocks response is missing data")
        return payload["data"]

    def check_connection(self) -> dict:
        """Verify authentication without returning personal profile information."""
        profile = self._get("/user/profile")
        return {"provider": "indstocks", "authenticated": True, "mode": "data-only",
                "orders_enabled": False, "nse_fno_onboarded": profile.get("is_nse_fno_onboarded"),
                "market_data_verified": False}

    def quotes(self, codes: list[str]) -> dict:
        """Return raw quotes with receipt time, never assert exchange freshness."""
        if not 1 <= len(codes) <= 1000 or any(not re.fullmatch(r"[A-Z]+_\d+", c) for c in codes):
            raise ValueError("Provide 1-1000 SEGMENT_TOKEN codes from the instruments master")
        data = self._get("/market/quotes/full", {"scrip-codes": ",".join(codes)})
        return {"data": data, "received_at": datetime.now(timezone.utc).isoformat(),
                "timestamp_verified": False, "missing_codes": sorted(set(codes) - data.keys())}

    def history(self, identifiers: list[str], start_ms: int, end_ms: int,
                interval: str = "1minute", expired: bool = False) -> dict:
        """Fetch one bounded OHLCV page; timestamps remain candle-open seconds."""
        limit = 50 if expired else 5
        pattern = r"[A-Z0-9]+" if expired else r"[A-Z]+_\d+"
        if not 1 <= len(identifiers) <= limit or any(not re.fullmatch(pattern, x) for x in identifiers):
            raise ValueError(f"Provide 1-{limit} valid instrument identifiers")
        if interval not in ("1minute", "5minute") or not 0 <= start_ms < end_ms or end_ms - start_ms > 7 * 86400000:
            raise ValueError("Use 1minute/5minute and a positive date range of at most 7 days (epoch milliseconds)")
        key = "trading-symbols" if expired else "scrip-codes"
        path = "/market/historical/" + ("expired/" if expired else "") + interval
        data = self._get(path, {key: ",".join(identifiers), "start_time": start_ms, "end_time": end_ms})
        return {"data": data, "missing_codes": sorted(set(identifiers) - data.keys()),
                "timestamp_unit": "seconds", "timestamp_label": "candle_open"}

    async def stream(self, instruments: list[str], mode: str = "quote") -> AsyncIterator[dict]:
        """Stream raw quote events; caller must handle gaps and reconnect explicitly."""
        if not 1 <= len(instruments) <= 3000 or any(not re.fullmatch(r"[A-Z]+:\d+", x) for x in instruments):
            raise ValueError("Provide 1-3000 SEGMENT:TOKEN instruments")
        if mode not in ("quote", "ltp"):
            raise ValueError("Unsupported market-data mode")
        token = self.token()
        try:
            async with websockets.connect(
                "wss://ws-prices.indstocks.com/api/v1/ws/prices",
                extra_headers={"Authorization": token}, open_timeout=10,
                ping_interval=20, ping_timeout=20, max_queue=32,
            ) as socket:
                await socket.send(json.dumps({"action": "subscribe", "mode": mode, "instruments": instruments}))
                async for raw in socket:
                    yield {"raw": decode_stream_event(raw), "received_at": datetime.now(timezone.utc).isoformat(),
                           "timestamp_verified": False}
        except Exception:
            raise INDstocksError("INDstocks stream stopped; treat this as a data gap before reconnecting") from None


client = INDstocksData()
