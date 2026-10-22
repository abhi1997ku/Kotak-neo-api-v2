"""Validated, contract-specific one-minute history for paper warm-up."""
from datetime import datetime, timedelta, time
from math import isfinite
from zoneinfo import ZoneInfo

import requests

from backend.paper_trading import OneMinuteCandle

INDIA = ZoneInfo("Asia/Kolkata")


def parse_history(payload, now):
    if payload.get("status") != "success" or payload.get("interval") != "1min":
        raise ValueError("Historical API returned no valid one-minute history")
    candles = {}
    for row in payload.get("data", {}).get("candles", []):
        stamp = datetime.fromisoformat(row[0])
        if stamp.tzinfo is None or stamp.second or stamp.microsecond:
            raise ValueError("Invalid historical candle timestamp")
        stamp = stamp.astimezone(INDIA)
        values = [float(x) for x in row[1:6]]
        if len(values) != 5 or not all(isfinite(x) for x in values):
            raise ValueError("Invalid historical OHLCV")
        o, h, l, c, v = values
        if min(o, h, l, c) <= 0 or h < max(o, l, c) or l > min(o, h, c) or v < 0 or int(v) != v:
            raise ValueError("Invalid historical OHLCV")
        if not time(9, 15) <= stamp.time() < time(15, 30) or stamp.weekday() >= 5:
            continue
        if stamp + timedelta(minutes=1) > now or stamp.date() < now.date() - timedelta(days=10):
            continue
        minute = int(stamp.timestamp() // 60)
        if minute in candles:
            raise ValueError("Duplicate historical candle")
        candles[minute] = OneMinuteCandle(minute, o, h, l, c, int(v))
    return [candles[k] for k in sorted(candles)]


def fetch_history(client, token, now):
    base = client.session.base_url
    if not base:
        raise ValueError("Broker historical base URL unavailable")
    response = requests.get(
        base.rstrip("/") + "/market-data/1.0/historical/details",
        params={"neosymbol": f"nse_fo|{token}", "interval": "1min",
                "fromdate": (now.date() - timedelta(days=10)).isoformat(), "todate": now.date().isoformat()},
        headers={"Authorization": client.config["consumer_key"], "Content-Type": "application/json"},
        timeout=10, allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"Historical API HTTP {response.status_code}")
    return parse_history(response.json(), now)
