"""Versioned, local model-trade observations for later offline training."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "training_data" / "swing_trades.sqlite3"
STRATEGY_VERSION = "hourly-breakout-minute-ema-v1"


def connect(path=DEFAULT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS observations (
        strategy TEXT NOT NULL, trade_id TEXT NOT NULL, digest TEXT NOT NULL,
        observed_at TEXT NOT NULL, data_as_of TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(strategy, trade_id, digest))""")
    db.execute("""CREATE TABLE IF NOT EXISTS trades (
        strategy TEXT NOT NULL, trade_id TEXT NOT NULL, symbol TEXT NOT NULL,
        interval_minutes INTEGER NOT NULL, status TEXT NOT NULL,
        first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        data_as_of TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(strategy, trade_id))""")
    return db


def save_trades(records, *, as_of, period_start, path=DEFAULT_PATH):
    now = datetime.now(timezone.utc).isoformat()
    db = connect(path)
    try:
        with db:
            for trade in records:
                payload = dict(trade, schema_version=1, strategy_version=STRATEGY_VERSION,
                               provenance="SIMULATED_CANDLE_REPLAY", provider="Yahoo Finance",
                               replay_period_start=period_start, cost_per_side_bps=2,
                               training_eligible=trade['status'] == 'CLOSED')
                # Features are nested separately from outcomes to prevent accidental
                # use of exit/peak/stop-history information as entry-time inputs.
                encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?)",
                           (STRATEGY_VERSION, trade['id'], digest, now, as_of, encoded))
                db.execute("""INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(strategy,trade_id) DO UPDATE SET
                    status=excluded.status, last_seen_at=excluded.last_seen_at,
                    data_as_of=excluded.data_as_of, payload=excluded.payload
                    WHERE excluded.data_as_of >= trades.data_as_of""",
                    (STRATEGY_VERSION, trade['id'], trade['symbol'], trade['trailing_interval_minutes'],
                     trade['status'], now, now, as_of, encoded))
        return {"saved": len(records), "strategy_version": STRATEGY_VERSION,
                "provenance": "SIMULATED_CANDLE_REPLAY"}
    finally:
        db.close()


def export_records(path=DEFAULT_PATH, *, closed_only=False):
    if not Path(path).exists():
        return []
    db = connect(path)
    try:
        query = "SELECT payload, first_seen_at, last_seen_at, data_as_of FROM trades"
        if closed_only:
            query += " WHERE status='CLOSED'"
        query += " ORDER BY trade_id"
        return [dict(json.loads(payload), first_seen_at=first, last_seen_at=last, data_as_of=as_of)
                for payload, first, last, as_of in db.execute(query)]
    finally:
        db.close()
