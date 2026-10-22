"""Transactional paper journal. Quote evidence stays local in SQLite."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def save_state(path: Path, state: dict) -> None:
    """Commit runtime recovery state and idempotent trade records together."""
    encoded = json.dumps(state, allow_nan=False)
    with sqlite3.connect(path, timeout=5) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS runtime (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY, entered_at TEXT NOT NULL, result TEXT NOT NULL,
            payload TEXT NOT NULL)""")
        db.execute("CREATE INDEX IF NOT EXISTS trades_entered ON trades(entered_at)")
        for trade in state.get("trades", []) + ([state["position"]] if state.get("position") else []):
            identity = "|".join(str(trade.get(k, "")) for k in ("entered_at", "code", "side"))
            key = hashlib.sha256(identity.encode()).hexdigest()
            db.execute("""INSERT INTO trades VALUES (?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET result=excluded.result,payload=excluded.payload
                WHERE trades.result != 'CLOSED_ESTIMATE'""",
                (key, trade["entered_at"], trade["result"], json.dumps(trade, allow_nan=False)))
        db.execute("INSERT OR REPLACE INTO runtime VALUES (1,?)", (encoded,))


def load_state(path: Path, legacy: Path) -> dict:
    """Recover committed state; migrate the old JSON only on first startup."""
    if path.exists():
        with sqlite3.connect(path, timeout=5) as db:
            exists = db.execute("SELECT name FROM sqlite_master WHERE name='runtime'").fetchone()
            row = db.execute("SELECT payload FROM runtime WHERE id=1").fetchone() if exists else None
            if row:
                return json.loads(row[0])
    state = json.loads(legacy.read_text()) if legacy.exists() else {"seen": [], "trades": [], "position": None}
    save_state(path, state)
    return state


def dashboard(path: Path, limit: int = 20, offset: int = 0) -> dict:
    """Read a consistent journal snapshot and all-time closed-trade gross metrics."""
    if not path.exists():
        return {"status": "NOT_STARTED", "orders_enabled": False, "position": None,
                "trades": [], "total": 0, "summary": {}}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT payload FROM runtime WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {}
        closed = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM trades WHERE result='CLOSED_ESTIMATE' ORDER BY entered_at DESC,id DESC")]
    pnl = [float(t.get("gross_pnl") or 0) for t in closed]
    wins = sum(p > 0 for p in pnl)
    state.update(trades=closed[offset:offset + limit], total=len(closed),
                 summary={"closed": len(closed), "wins": wins, "losses": sum(p < 0 for p in pnl),
                          "breakeven": sum(p == 0 for p in pnl),
                          "win_rate": round(100 * wins / len(pnl), 2) if pnl else None,
                          "gross_pnl": round(sum(pnl), 2)})
    state.pop("seen", None)
    for trade in state["trades"] + ([state["position"]] if state.get("position") else []):
        trade.pop("entry_snapshot", None)
        trade.pop("exit_snapshot", None)
    return state
