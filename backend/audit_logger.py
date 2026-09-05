from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class AuditLogger:
    """Lightweight local audit trail for order events on the developer machine."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else Path(__file__).resolve().parent / "trading_audit.db"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    action TEXT NOT NULL,
                    order_id TEXT,
                    symbol TEXT,
                    payload TEXT,
                    status TEXT,
                    details TEXT
                )
                """
            )
            conn.commit()

    def log_event(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        ts = datetime.now(timezone.utc).isoformat()
        payload = {
            "order_id": kwargs.get("order_id"),
            "symbol": kwargs.get("symbol"),
            "status": kwargs.get("status", "unknown"),
            "details": kwargs.get("details"),
            "payload": kwargs.get("payload", {}),
        }
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO order_actions (ts, action, order_id, symbol, payload, status, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    action,
                    payload["order_id"],
                    payload["symbol"],
                    str(payload["payload"]),
                    payload["status"],
                    payload["details"],
                ),
            )
            conn.commit()
        return payload

    def latest_events(self, limit: int = 20) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT ts, action, order_id, symbol, payload, status, details FROM order_actions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "ts": row[0],
                "action": row[1],
                "order_id": row[2],
                "symbol": row[3],
                "payload": row[4],
                "status": row[5],
                "details": row[6],
            }
            for row in rows
        ]
