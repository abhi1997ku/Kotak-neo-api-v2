from __future__ import annotations
import csv, io, json, sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

class PaperJournal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else Path(__file__).resolve().parent / "paper_trading.db"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS paper_trades (id INTEGER PRIMARY KEY, opened_at TEXT, closed_at TEXT, symbol TEXT, quantity INTEGER, entry_price REAL, exit_price REAL, points_pnl REAL, pnl REAL, exit_reason TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_day_candles (trade_date TEXT NOT NULL, minute INTEGER NOT NULL, opened_at TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, PRIMARY KEY (trade_date, minute))")
            db.execute("CREATE TABLE IF NOT EXISTS paper_day_events (id INTEGER PRIMARY KEY, trade_date TEXT NOT NULL, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, direction TEXT, ltp REAL, details TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_pattern_samples (trade_date TEXT NOT NULL, minute INTEGER NOT NULL, opened_at TEXT NOT NULL, features_json TEXT NOT NULL, forward_return_5m REAL, PRIMARY KEY (trade_date, minute))")
            db.execute("CREATE TABLE IF NOT EXISTS paper_contract_candles (contract TEXT NOT NULL, minute INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume INTEGER, PRIMARY KEY(contract, minute))")
            db.execute("CREATE TABLE IF NOT EXISTS paper_runtime (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def save_position(self, position: dict | None) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO paper_runtime VALUES ('position', ?)", (json.dumps(position),))

    def load_position(self) -> dict | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM paper_runtime WHERE key='position'").fetchone()
        return json.loads(row[0]) if row else None

    def open_position(self, trade: dict, token: str, trade_date: str, details: str, strategy: str) -> None:
        """Persist position and daily entry count together before acknowledging entry."""
        payload = {"trade":trade,"token":token,"last_premium":trade["entry_price"],"strategy":strategy}
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO paper_runtime VALUES ('position',?)", (json.dumps(payload),))
            db.execute("INSERT INTO paper_day_events (trade_date,occurred_at,event_type,direction,ltp,details) VALUES (?,?,?,?,?,?)",
                       (trade_date,trade["opened_at"],"trade_opened",trade.get("direction"),trade["entry_price"],details))

    def realized_pnl_for_day(self, trade_date: str) -> float:
        with sqlite3.connect(self.path) as db:
            return float(db.execute("SELECT COALESCE(SUM(pnl),0) FROM paper_trades WHERE substr(opened_at,1,10)=?", (trade_date,)).fetchone()[0])

    def last_close_for_day(self, trade_date: str) -> str | None:
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT MAX(closed_at) FROM paper_trades WHERE substr(closed_at,1,10)=?", (trade_date,)).fetchone()[0]

    def cache_candles(self, contract: str, candles: list) -> None:
        with sqlite3.connect(self.path) as db:
            db.executemany("INSERT OR REPLACE INTO paper_contract_candles VALUES (?,?,?,?,?,?,?)",
                           [(contract, c.minute, c.open, c.high, c.low, c.close, c.volume) for c in candles])

    def cached_candles(self, contract: str, now: datetime) -> list:
        from backend.paper_trading import OneMinuteCandle
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT minute,open,high,low,close,volume FROM paper_contract_candles WHERE contract=? AND minute>=? AND minute<? ORDER BY minute",
                              (contract, int(now.timestamp() // 60) - 10*1440, int(now.timestamp() // 60))).fetchall()
        return [OneMinuteCandle(*row) for row in rows]

    def begin_day(self, trade_date: str) -> None:
        """Keep screen audit data for today and up to 90 days of model samples."""
        cutoff = (date.fromisoformat(trade_date) - timedelta(days=90)).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM paper_contract_candles WHERE minute < ?", (int(datetime.fromisoformat(cutoff).replace(tzinfo=timezone.utc).timestamp() // 60),))
            db.execute("DELETE FROM paper_day_candles WHERE trade_date < ?", (cutoff,))
            db.execute("DELETE FROM paper_day_events WHERE trade_date < ?", (cutoff,))
            db.execute("DELETE FROM paper_pattern_samples WHERE trade_date < ?", (cutoff,))

    def record_candle(self, trade_date: str, candle: Any) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO paper_day_candles (trade_date,minute,opened_at,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?,?)", (trade_date, candle.minute, datetime.fromtimestamp(candle.minute * 60, timezone.utc).isoformat(), candle.open, candle.high, candle.low, candle.close, candle.volume))

    def record_pattern_sample(self, trade_date: str, minute: int, features: dict[str, Any]) -> None:
        opened_at = datetime.fromtimestamp(minute * 60, timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO paper_pattern_samples (trade_date,minute,opened_at,features_json) VALUES (?,?,?,?) "
                "ON CONFLICT(trade_date,minute) DO UPDATE SET features_json=excluded.features_json",
                (trade_date, minute, opened_at, json.dumps(features, sort_keys=True, separators=(",", ":"))),
            )

    def label_pattern_sample(self, trade_date: str, minute: int, forward_return_5m: float) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE paper_pattern_samples SET forward_return_5m=? WHERE trade_date=? AND minute=?",
                (forward_return_5m, trade_date, minute),
            )

    def pattern_sample_status(self) -> dict[str, int]:
        with sqlite3.connect(self.path) as db:
            total, labeled, days = db.execute(
                "SELECT COUNT(*), SUM(CASE WHEN forward_return_5m IS NOT NULL THEN 1 ELSE 0 END), COUNT(DISTINCT trade_date) "
                "FROM paper_pattern_samples"
            ).fetchone()
        return {"samples": int(total or 0), "labeled_samples": int(labeled or 0), "trading_days": int(days or 0)}

    def losing_trades_for_day(self, trade_date: str) -> int:
        """Count realized losing paper trades for the session, surviving restarts."""
        with sqlite3.connect(self.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM paper_trades WHERE substr(opened_at,1,10)=? AND COALESCE(pnl,0)<0",
                (trade_date,),
            ).fetchone()[0]
        return int(count or 0)

    def entries_for_day(self, trade_date: str) -> int:
        """Count virtual entries today so the trade cap survives backend restarts."""
        with sqlite3.connect(self.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM paper_day_events WHERE trade_date=? AND event_type='trade_opened'",
                (trade_date,),
            ).fetchone()[0]
        return int(count or 0)

    def record_event(self, trade_date: str, event_type: str, details: str = "", direction: str | None = None, ltp: float | None = None) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO paper_day_events (trade_date,occurred_at,event_type,direction,ltp,details) VALUES (?,?,?,?,?,?)", (trade_date, datetime.now(timezone.utc).isoformat(), event_type, direction, ltp, details))

    def day_audit(self, trade_date: str) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            candles = db.execute("SELECT opened_at,open,high,low,close,volume FROM paper_day_candles WHERE trade_date=? ORDER BY minute", (trade_date,)).fetchall()
            events = db.execute("SELECT occurred_at,event_type,direction,ltp,details FROM paper_day_events WHERE trade_date=? ORDER BY id", (trade_date,)).fetchall()
        return {"trade_date": trade_date, "candles": [dict(zip(("opened_at","open","high","low","close","volume"), row)) for row in candles], "events": [dict(zip(("occurred_at","event_type","direction","ltp","details"), row)) for row in events]}

    def day_candles(self, trade_date: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT minute,open,high,low,close,volume FROM paper_day_candles WHERE trade_date=? ORDER BY minute", (trade_date,)).fetchall()
        return [dict(zip(("minute", "open", "high", "low", "close", "volume"), row)) for row in rows]
    def close_trade(self, trade: dict[str, Any], *, clear_position: bool = False) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO paper_trades (opened_at,closed_at,symbol,quantity,entry_price,exit_price,points_pnl,pnl,exit_reason) SELECT ?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM paper_trades WHERE opened_at=? AND symbol=?)",
                       tuple(trade.get(k) for k in ("opened_at","closed_at","symbol","quantity","entry_price","exit_price","points_pnl","unrealized_pnl","exit_reason"))+(trade.get("opened_at"),trade.get("symbol")))
            if clear_position:
                db.execute("DELETE FROM paper_runtime WHERE key='position'")
    def report(self) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db: rows=db.execute("SELECT opened_at,closed_at,symbol,quantity,entry_price,exit_price,points_pnl,pnl,exit_reason FROM paper_trades ORDER BY id DESC").fetchall()
        keys=("opened_at","closed_at","symbol","quantity","entry_price","exit_price","points_pnl","pnl","exit_reason"); trades=[dict(zip(keys,r)) for r in rows]; wins=sum(1 for t in trades if (t["pnl"] or 0)>0)
        return {"trades":trades,"trade_count":len(trades),"wins":wins,"total_pnl":round(sum(t["pnl"] or 0 for t in trades),2),"win_rate":round(100*wins/len(trades),2) if trades else 0}
    def csv(self) -> str:
        out=io.StringIO(); keys=("opened_at","closed_at","symbol","quantity","entry_price","exit_price","points_pnl","pnl","exit_reason"); writer=csv.DictWriter(out,fieldnames=keys); writer.writeheader(); writer.writerows(self.report()["trades"]); return out.getvalue()
