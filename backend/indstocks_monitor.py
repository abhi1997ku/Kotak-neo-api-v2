"""Continuous five-minute order-block 2R signal observation. Never fills or places orders.

Run: python -m backend.indstocks_monitor
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pandas as pd

from backend.indstocks_data import client



ROOT = Path(__file__).resolve().parent / "data"


def completed_bars(candles: list[dict], now: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Exclude partial minutes and require five actual minutes per context bar."""
    bars = pd.DataFrame(candles)
    if bars.empty:
        raise ValueError("No historical candles available")
    bars.index = pd.to_datetime(bars.pop("ts"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    bars = bars.rename(columns=dict(o="Open", h="High", l="Low", c="Close", v="Volume"))
    bars = bars.sort_index()
    if not bars.index.is_unique:
        raise ValueError("Duplicate candle timestamps")
    bars = bars[bars.index + pd.Timedelta(minutes=1) <= now].between_time("09:15", "15:29")
    five = bars.resample("5min").agg(dict(Open="first", High="max", Low="min", Close="last", Volume="sum"))
    count = bars.Close.resample("5min").count()
    five = five[(count == 5) & (five.index + pd.Timedelta(minutes=5) <= now)]
    return bars, five


def resolve_future(now: pd.Timestamp) -> dict:
    """Use downloaded master, refusing expired contracts or stale metadata."""
    path = ROOT / "indstocks_fno.json"
    modified = pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC").tz_convert("Asia/Kolkata")
    if modified.date() != now.date():
        raise ValueError("Refresh INDstocks instruments master for this trading day")
    rows = json.loads(path.read_text())
    candidates = [r for r in rows if r.get("INSTRUMENT_NAME") == "FUTIDX"
                  and r.get("EXCH") == "NSE" and r.get("TRADING_SYMBOL", "").startswith("BANKNIFTY")
                  and pd.to_datetime(r["EXPIRY_DATE"]).date() >= now.date()]
    if not candidates:
        raise ValueError("No unexpired BANKNIFTY future")
    return min(candidates, key=lambda r: pd.to_datetime(r["EXPIRY_DATE"]))


def refresh_master(now: pd.Timestamp) -> None:
    """Refresh the read-only F&O CSV once per local day, with atomic persistence."""
    import csv
    import io
    import requests
    from backend.indstocks_data import INDstocksError
    path = ROOT / "indstocks_fno.json"
    if path.exists() and pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC").tz_convert("Asia/Kolkata").date() == now.date():
        return
    try:
        response = requests.get("https://api.indstocks.com/market/instruments", params={"source": "fno"},
                                headers={"Authorization": client.token()}, timeout=(5, 20), allow_redirects=False)
        if response.status_code != 200:
            raise INDstocksError(f"Instrument master HTTP {response.status_code}")
        rows = list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff'))))
        if not rows or not {"SECURITY_ID", "TRADING_SYMBOL", "EXPIRY_DATE", "INSTRUMENT_NAME", "EXCH"} <= rows[0].keys():
            raise INDstocksError("Invalid instrument master CSV")
    except requests.RequestException:
        raise INDstocksError("Instrument master network failure") from None
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rows))
    temporary.replace(path)


def observe(database: Path, now: pd.Timestamp) -> dict:
    """Persist idempotent observations, explicitly distinguishing replayed setups."""
    future = resolve_future(now)
    code = "NFO_" + future["SECURITY_ID"]
    start = now.normalize() - pd.Timedelta(days=6)
    history = client.history([code], int(start.timestamp() * 1000), int(now.timestamp() * 1000))
    one, five = completed_bars(history["data"].get(code, {}).get("candles") or [], now)
    from backend.order_block_strategy import signals, STRATEGY
    setups = signals(five, now)
    records = [{**s, "entry_price": s["entry"], "stop_price": s["stop"],
                "target_price": s["target"]} for s in setups]
    accepted = pd.DataFrame(records)
    rejected = pd.DataFrame()
    current = one[one.index.date == now.date()]
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS observations (contract TEXT, timestamp TEXT, side TEXT, accepted INTEGER, payload TEXT, observed_at TEXT, PRIMARY KEY(contract,timestamp,side))")
        for frame, valid in ((accepted, 1), (rejected, 0)):
            for record in json.loads(frame.to_json(orient="records", date_format="iso")):
                stamp = pd.Timestamp(record["timestamp"])
                if stamp.tz_convert("Asia/Kolkata").date() != now.date():
                    continue
                record["execution_status"] = "SETUP_RECORDED" if valid else "REJECTED"
                record["historical_observation"] = (now - stamp).total_seconds() > 60
                db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?)",
                           (code, record["timestamp"], record["side"], valid, json.dumps(record), now.isoformat()))
        counts = db.execute("SELECT accepted,COUNT(*) FROM observations WHERE substr(observed_at,1,10)=? GROUP BY accepted", (str(now.date()),)).fetchall()
    return {"strategy": "order_block_5m_midpoint_2r_v1", "target_r": 2.0, "mode": "signal-observation-only", "contract": future["TRADING_SYMBOL"],
            "checked_at": now.isoformat(), "last_closed_bar": str(one.index[-1]) if len(one) else None,
            "today_bars": len(current), "accepted": dict(counts).get(1, 0), "rejected": dict(counts).get(0, 0),
            "orders_enabled": False, "fills_enabled": False, "fill_blocker": "UNVERIFIED_QUOTE_TIME"}


def main() -> None:
    """Poll once per minute during the session; persist sanitized health state."""
    ROOT.mkdir(exist_ok=True)
    # A database write lock prevents duplicate monitor processes.
    guard = sqlite3.connect(ROOT / "indstocks_monitor_lock.sqlite", timeout=0)
    guard.execute("CREATE TABLE IF NOT EXISTS lock (id INTEGER)")
    guard.commit()
    guard.execute("BEGIN EXCLUSIVE")
    while True:
        now = pd.Timestamp.now(tz="Asia/Kolkata")
        try:
            if now.weekday() >= 5 or not "09:15" <= now.strftime("%H:%M") <= "15:35":
                state = {"mode": "signal-observation-only", "state": "outside_session", "checked_at": now.isoformat(), "fills_enabled": False}
            else:
                refresh_master(now)
                state = observe(ROOT / "indstocks_ob_observations.sqlite", now)
        except Exception as exc:
            # Provider error bodies and credentials must never appear in logs.
            state = {"state": "error", "error_type": type(exc).__name__, "checked_at": now.isoformat(), "fills_enabled": False}
        temporary = ROOT / "indstocks_ob_status.tmp"
        state.update(strategy="order_block_5m_midpoint_2r_v1", target_r=2.0, orders_enabled=False)
        temporary.write_text(json.dumps(state))
        temporary.replace(ROOT / "indstocks_ob_status.json")
        print(json.dumps(state), flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()

