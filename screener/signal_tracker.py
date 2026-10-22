"""Persistent paper tracking for swing research signals; never places orders."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Callable
from zoneinfo import ZoneInfo

from screener.data_fetcher import fetch_data_batch

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "screener_results.db"
TRACKING_SESSIONS = 10
OPEN_STATUSES = ("PENDING_ENTRY", "OPEN")
TERMINAL_STATUSES = ("TARGET_1", "TARGET_2", "TARGET_3", "STOP_LOSS", "TIME_EXIT", "MISSED_ENTRY")
INDIA_TZ = ZoneInfo("Asia/Kolkata")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def _candidate_groups(result: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups = [
        ("rule_based", result.get("short_swing") or []),
        ("rule_based", result.get("weekly_swing") or []),
        ("rule_based", result.get("monthly_swing") or []),
    ]
    ai = result.get("ai_pattern_signals") or {}
    groups.extend([
        ("ai_pattern", ai.get("short_swing") or []),
        ("ai_pattern", ai.get("weekly_swing") or []),
        ("ai_pattern", ai.get("monthly_swing") or []),
    ])
    return groups


def _frame_bars(frame: Any, signal_date: str, current_date: str) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    bars: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        bar_date = _iso_date(index)
        # The scan's current daily candle may have happened before the signal
        # was generated or may still be incomplete. Only evaluate later,
        # completed sessions to avoid look-ahead from the signal day.
        if bar_date <= signal_date or bar_date >= current_date:
            continue
        values = {str(key).lower(): value for key, value in row.items()}
        opened = _number(values.get("open"))
        high = _number(values.get("high"))
        low = _number(values.get("low"))
        close = _number(values.get("close"))
        if None in (opened, high, low, close):
            continue
        bars.append({"date": bar_date, "open": opened, "high": high, "low": low, "close": close})
    return sorted(bars, key=lambda bar: bar["date"])


def _evaluate_signal(signal: dict[str, Any], bars: list[dict[str, Any]], current_price: float | None) -> dict[str, Any]:
    entry = float(signal["entry"])
    stop = float(signal["stop_loss"])
    direction = 1.0 if signal["direction"] == "LONG" else -1.0
    risk = abs(entry - stop)
    zone_low = _number(signal.get("entry_zone_low"))
    zone_high = _number(signal.get("entry_zone_high"))
    zone_low = entry if zone_low is None else zone_low
    zone_high = entry if zone_high is None else zone_high
    zone_low, zone_high = sorted((zone_low, zone_high))
    targets = [
        _number(signal.get("target_1")),
        _number(signal.get("target_2")),
        _number(signal.get("target_3")),
    ]

    status = "PENDING_ENTRY"
    entry_date = None
    exit_date = None
    exit_price = None
    exit_r = None
    ambiguous_bar = False
    entered = False
    max_favorable_r = 0.0
    max_adverse_r = 0.0
    latest_close = bars[-1]["close"] if bars else None
    used_bars = bars[:TRACKING_SESSIONS]

    for bar in used_bars:
        if not entered:
            touches_entry_zone = bar["high"] >= zone_low and bar["low"] <= zone_high
            if not touches_entry_zone:
                continue
            entered = True
            entry_date = bar["date"]
            status = "OPEN"

        favorable_price = bar["high"] if direction > 0 else bar["low"]
        adverse_price = bar["low"] if direction > 0 else bar["high"]
        max_favorable_r = max(max_favorable_r, direction * (favorable_price - entry) / risk)
        max_adverse_r = min(max_adverse_r, direction * (adverse_price - entry) / risk)

        stop_hit = bar["low"] <= stop if direction > 0 else bar["high"] >= stop
        hit_targets = [
            index + 1
            for index, target in enumerate(targets)
            if target is not None and (bar["high"] >= target if direction > 0 else bar["low"] <= target)
        ]
        if stop_hit:
            # Daily OHLC cannot reveal the order of same-session touches.
            # Use stop-first when both stop and target are inside one candle.
            ambiguous_bar = bool(hit_targets)
            status = "STOP_LOSS"
            exit_date = bar["date"]
            exit_price = min(stop, bar["open"]) if direction > 0 else max(stop, bar["open"])
            exit_r = direction * (exit_price - entry) / risk
            break
        if hit_targets:
            target_level = max(hit_targets)
            status = f"TARGET_{target_level}"
            exit_date = bar["date"]
            exit_price = targets[target_level - 1]
            exit_r = direction * (exit_price - entry) / risk
            break

    if entered and status == "OPEN" and len(used_bars) >= TRACKING_SESSIONS:
        status = "TIME_EXIT"
        exit_date = used_bars[-1]["date"]
        exit_price = used_bars[-1]["close"]
        exit_r = direction * (exit_price - entry) / risk
    elif not entered and len(used_bars) >= TRACKING_SESSIONS:
        status = "MISSED_ENTRY"

    mark_price = current_price if current_price is not None else latest_close
    current_return_pct = None
    current_r = None
    if entered and status == "OPEN" and mark_price is not None and entry > 0:
        current_return_pct = direction * (mark_price - entry) / entry * 100
        current_r = direction * (mark_price - entry) / risk

    return {
        "status": status,
        "entry_triggered": entered,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "exit_r_multiple": exit_r,
        "current_return_pct": current_return_pct,
        "current_r_multiple": current_r,
        "max_favorable_r": max_favorable_r if entered else None,
        "max_adverse_r": max_adverse_r if entered else None,
        "ambiguous_bar": ambiguous_bar,
        "elapsed_sessions": len(used_bars),
        "latest_close": latest_close,
        "latest_bar_date": bars[-1]["date"] if bars else None,
        "mark_price": mark_price,
    }


class SignalPerformanceTracker:
    """Store scan outputs once and evaluate them against future daily OHLCV."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS swing_signal_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_key TEXT NOT NULL UNIQUE,
                    generated_at TEXT NOT NULL,
                    signal_date TEXT NOT NULL,
                    universe TEXT NOT NULL,
                    source TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    company TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    action TEXT NOT NULL,
                    setup TEXT NOT NULL,
                    entry REAL NOT NULL,
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    stop_loss REAL NOT NULL,
                    target_1 REAL NOT NULL,
                    target_2 REAL,
                    target_3 REAL,
                    setup_score REAL,
                    status TEXT NOT NULL DEFAULT 'PENDING_ENTRY',
                    entry_triggered INTEGER NOT NULL DEFAULT 0,
                    entry_date TEXT,
                    exit_date TEXT,
                    exit_price REAL,
                    exit_r_multiple REAL,
                    current_return_pct REAL,
                    current_r_multiple REAL,
                    max_favorable_r REAL,
                    max_adverse_r REAL,
                    latest_close REAL,
                    latest_ltp REAL,
                    latest_bar_date TEXT,
                    elapsed_sessions INTEGER NOT NULL DEFAULT 0,
                    tracking_sessions INTEGER NOT NULL DEFAULT 10,
                    ambiguous_bar INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS swing_signal_bars (
                    signal_id INTEGER NOT NULL,
                    bar_date TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    PRIMARY KEY (signal_id, bar_date),
                    FOREIGN KEY (signal_id) REFERENCES swing_signal_performance(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_swing_signal_date ON swing_signal_performance(signal_date DESC, id DESC)"
            )
            connection.commit()

    def record_scan(self, result: dict[str, Any], universe: str) -> dict[str, int]:
        generated_at = str(result.get("generated_at") or datetime.now(INDIA_TZ).isoformat(timespec="seconds"))
        inserted = 0
        skipped = 0
        with self._connect() as connection:
            for source, candidates in _candidate_groups(result):
                for candidate in candidates:
                    action = str(candidate.get("action") or "").strip().upper()
                    direction = str(candidate.get("direction") or "").strip().upper()
                    entry = _number(candidate.get("entry"))
                    stop = _number(candidate.get("stop_loss"))
                    target_1 = _number(candidate.get("target_1"))
                    symbol = str(candidate.get("symbol") or "").strip().upper()
                    if action in {"AVOID", "NO TRADE"} or direction not in {"LONG", "SHORT"} or not symbol:
                        skipped += 1
                        continue
                    if entry is None or stop is None or target_1 is None or entry == stop:
                        skipped += 1
                        continue

                    signal_time = str((candidate.get("source_dates") or {}).get("signal_generated_at") or generated_at)
                    signal_date = _iso_date(signal_time)
                    timeframe = str(candidate.get("timeframe") or "swing")
                    setup = str(candidate.get("setup") or "")
                    identity = "|".join((signal_date, source, symbol, timeframe, direction, setup))
                    signal_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    try:
                        score = float(candidate.get("setup_score"))
                    except (TypeError, ValueError):
                        score = None
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO swing_signal_performance (
                            signal_key, generated_at, signal_date, universe, source, symbol, company,
                            timeframe, direction, action, setup, entry, entry_zone_low, entry_zone_high,
                            stop_loss, target_1, target_2, target_3, setup_score, tracking_sessions
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            signal_key,
                            signal_time,
                            signal_date,
                            str(universe or "nifty500"),
                            source,
                            symbol,
                            str(candidate.get("company") or symbol),
                            timeframe,
                            direction,
                            action,
                            setup,
                            entry,
                            _number(candidate.get("entry_zone_low")),
                            _number(candidate.get("entry_zone_high")),
                            stop,
                            target_1,
                            _number(candidate.get("target_2")),
                            _number(candidate.get("target_3")),
                            score,
                            TRACKING_SESSIONS,
                        ),
                    )
                    if cursor.rowcount:
                        inserted += 1
                    else:
                        skipped += 1
            connection.commit()
        return {"new_signals_recorded": inserted, "duplicate_or_skipped": skipped}

    def refresh(self, quote_fetcher: Callable[[list[str]], dict[str, float]] | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            active = [dict(row) for row in connection.execute(
                "SELECT * FROM swing_signal_performance WHERE status IN (?, ?) ORDER BY signal_date DESC, id DESC",
                OPEN_STATUSES,
            ).fetchall()]

        symbols = list(dict.fromkeys(row["symbol"] for row in active))
        frames: dict[str, Any] = {}
        if symbols:
            try:
                frames = fetch_data_batch(symbols, days=90)
            except Exception as exc:
                logger.warning("Could not refresh swing-signal daily bars: %s", exc)

        quotes: dict[str, float] = {}
        if symbols and quote_fetcher:
            try:
                # Kotak's quote endpoint caps one request at 50 symbols. Current
                # LTP is a convenience mark; daily history tracks every signal.
                quotes = {str(key).upper(): value for key, value in quote_fetcher(symbols[:50]).items()}
            except Exception as exc:
                logger.info("Kotak marks unavailable for signal tracking; daily closes will be used: %s", exc)

        current_date = datetime.now(INDIA_TZ).date().isoformat()
        with self._connect() as connection:
            for signal in active:
                fetched_bars = _frame_bars(frames.get(signal["symbol"]), signal["signal_date"], current_date)
                fetched_bars = fetched_bars[:TRACKING_SESSIONS]
                for bar in fetched_bars:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO swing_signal_bars (signal_id, bar_date, open, high, low, close)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (signal["id"], bar["date"], bar["open"], bar["high"], bar["low"], bar["close"]),
                    )
                bars = [dict(row) for row in connection.execute(
                    "SELECT bar_date AS date, open, high, low, close FROM swing_signal_bars "
                    "WHERE signal_id = ? ORDER BY bar_date LIMIT ?",
                    (signal["id"], TRACKING_SESSIONS),
                ).fetchall()]
                mark = _number(quotes.get(signal["symbol"]))
                if mark is None and bars:
                    mark = bars[-1]["close"]
                if mark is None:
                    mark = _number(signal.get("latest_ltp")) or _number(signal.get("latest_close"))
                evaluated = _evaluate_signal(signal, bars, mark)
                connection.execute(
                    """
                    UPDATE swing_signal_performance SET
                        status = ?, entry_triggered = ?, entry_date = ?, exit_date = ?, exit_price = ?,
                        exit_r_multiple = ?, current_return_pct = ?, current_r_multiple = ?,
                        max_favorable_r = ?, max_adverse_r = ?, latest_close = ?, latest_ltp = ?,
                        latest_bar_date = ?, elapsed_sessions = ?, ambiguous_bar = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        evaluated["status"],
                        int(evaluated["entry_triggered"]),
                        evaluated["entry_date"],
                        evaluated["exit_date"],
                        evaluated["exit_price"],
                        evaluated["exit_r_multiple"],
                        evaluated["current_return_pct"],
                        evaluated["current_r_multiple"],
                        evaluated["max_favorable_r"],
                        evaluated["max_adverse_r"],
                        evaluated["latest_close"],
                        mark,
                        evaluated["latest_bar_date"],
                        evaluated["elapsed_sessions"],
                        int(evaluated["ambiguous_bar"]),
                        signal["id"],
                    ),
                )
            connection.commit()

        return self._snapshot()

    def _snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM swing_signal_performance ORDER BY signal_date DESC, id DESC LIMIT 100"
            ).fetchall()]

        cutoff = (datetime.now(INDIA_TZ).date() - timedelta(days=30)).isoformat()
        recent = [row for row in rows if row["signal_date"] >= cutoff]
        open_signals = [row for row in recent if row["status"] in OPEN_STATUSES]
        entered = [row for row in recent if row["entry_triggered"]]
        completed = [row for row in recent if row["status"] in TERMINAL_STATUSES and row["status"] != "MISSED_ENTRY"]
        resolved_returns = [_number(row.get("exit_r_multiple")) for row in completed]
        resolved_returns = [value for value in resolved_returns if value is not None]
        wins = sum(1 for value in resolved_returns if value > 0)
        losses = sum(1 for value in resolved_returns if value <= 0)
        win_rate = wins / len(resolved_returns) * 100 if resolved_returns else None
        avg_r = mean(resolved_returns) if resolved_returns else None
        by_source: dict[str, dict[str, Any]] = {}
        for source in ("rule_based", "ai_pattern"):
            source_rows = [row for row in recent if row["source"] == source]
            source_resolved = [
                _number(row.get("exit_r_multiple"))
                for row in source_rows
                if row["status"] in TERMINAL_STATUSES and row["status"] != "MISSED_ENTRY"
            ]
            source_resolved = [value for value in source_resolved if value is not None]
            source_wins = sum(1 for value in source_resolved if value > 0)
            by_source[source] = {
                "tracked_signals": len(source_rows),
                "open_signals": sum(1 for row in source_rows if row["status"] in OPEN_STATUSES),
                "completed": len(source_resolved),
                "wins": source_wins,
                "losses": len(source_resolved) - source_wins,
                "win_rate_pct": source_wins / len(source_resolved) * 100 if source_resolved else None,
                "average_r_multiple": mean(source_resolved) if source_resolved else None,
            }

        return {
            "as_of": datetime.now(INDIA_TZ).isoformat(timespec="seconds"),
            "tracking_sessions": TRACKING_SESSIONS,
            "price_source": "Yahoo Finance completed daily OHLCV; Kotak cash-market LTP when available",
            "assumption": "Underlying-share levels only; no F&O premium, fees, slippage, or intraday path. If one daily bar touches stop and target, stop is counted first.",
            "summary": {
                "tracked_signals": len(recent),
                "open_signals": len(open_signals),
                "entry_triggered": len(entered),
                "completed": len(completed),
                "wins": wins,
                "losses": losses,
                "missed_entries": sum(1 for row in recent if row["status"] == "MISSED_ENTRY"),
                "win_rate_pct": win_rate,
                "average_r_multiple": avg_r,
                "by_source": by_source,
            },
            "signals": recent,
        }
