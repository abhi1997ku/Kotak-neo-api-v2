"""Persist manually supplied market research feeds for the swing scanner.

This module accepts user-provided reports/CSVs only. It does not fetch data from
NSE or any other exchange website.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "screener_market_feeds.db"
_DB_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS institutional_flows (
            report_date TEXT NOT NULL,
            category TEXT NOT NULL,
            buy_value_cr REAL,
            sell_value_cr REAL,
            net_value_cr REAL,
            source_file TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (report_date, category)
        );
        CREATE TABLE IF NOT EXISTS delivery_positions (
            report_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            traded_quantity REAL,
            deliverable_quantity REAL,
            delivery_pct REAL,
            source_file TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (report_date, symbol)
        );
        CREATE TABLE IF NOT EXISTS corporate_events (
            symbol TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            imported_at TEXT NOT NULL,
            PRIMARY KEY (symbol, event_date, event)
        );
        """
    )
    return connection


def _cell(value: Any) -> str:
    return str(value or "").replace("\ufeff", "").strip()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _cell(value).lower())


def _amount(value: Any) -> float | None:
    text = _cell(value).replace(",", "").replace("₹", "").replace("%", "")
    if not text or text in {"-", "na", "n/a", "null"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("() ")
    try:
        parsed = float(text)
        return -parsed if negative else parsed
    except ValueError:
        return None


def _parse_date(value: Any) -> str | None:
    text = _cell(value)
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y",
        "%d%m%Y", "%Y%m%d", "%d-%b-%y", "%d/%b/%Y",
    ):
        try:
            return datetime.strptime(text.upper(), fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _rows(content: str) -> list[list[str]]:
    lines = [line for line in content.lstrip("\ufeff").splitlines() if line.strip()]
    if not lines:
        return []
    delimiter = "|" if sum(line.count("|") for line in lines[:30]) > sum(line.count(",") for line in lines[:30]) else ","
    return [[_cell(cell) for cell in row] for row in csv.reader(lines, delimiter=delimiter)]


def _find_column(header: list[str], *patterns: str) -> int | None:
    normalized = [_key(cell) for cell in header]
    for pattern in patterns:
        key = _key(pattern)
        exact = next((index for index, item in enumerate(normalized) if item == key), None)
        if exact is not None:
            return exact
    for pattern in patterns:
        key = _key(pattern)
        if key:
            match = next((index for index, item in enumerate(normalized) if key in item), None)
            if match is not None:
                return match
    return None


def _fii_rows(content: str, filename: str) -> list[dict[str, Any]]:
    rows = _rows(content)
    for index, header in enumerate(rows):
        category_col = _find_column(header, "category", "investor category", "institution type")
        date_col = _find_column(header, "date", "trade date", "report date")
        buy_col = _find_column(header, "buy value", "buyvalue", "purchases")
        sell_col = _find_column(header, "sell value", "sellvalue", "sales")
        net_col = _find_column(header, "net value", "netvalue", "net investment")
        if None in (category_col, date_col, buy_col, sell_col, net_col):
            continue
        output = []
        for row in rows[index + 1:]:
            if max(category_col, date_col, buy_col, sell_col, net_col) >= len(row):
                continue
            category_raw = row[category_col].upper()
            category = "FII/FPI" if "FII" in category_raw or "FPI" in category_raw else "DII" if "DII" in category_raw else None
            report_date = _parse_date(row[date_col])
            if not category or not report_date:
                continue
            output.append({
                "report_date": report_date,
                "category": category,
                "buy_value_cr": _amount(row[buy_col]),
                "sell_value_cr": _amount(row[sell_col]),
                "net_value_cr": _amount(row[net_col]),
                "source_file": filename,
            })
        if output:
            return output
    raise ValueError("Could not read FII/DII rows. Use the NSE report CSV with Category, Date, Buy Value, Sell Value, and Net Value columns.")


def _delivery_rows(content: str, filename: str) -> list[dict[str, Any]]:
    rows = _rows(content)
    filename_date = None
    match = re.search(r"MTO[_-]?(\d{8})", filename, re.IGNORECASE)
    if match:
        filename_date = _parse_date(match.group(1))

    output: list[dict[str, Any]] = []
    for index, header in enumerate(rows):
        symbol_col = _find_column(header, "symbol", "security symbol", "name of security")
        trade_col = _find_column(header, "quantity traded", "traded quantity", "traded qty")
        delivery_col = _find_column(header, "deliverable quantity", "delivery quantity", "deliverable qty")
        pct_col = _find_column(header, "% of deliverable quantity to traded quantity", "delivery percentage", "deliverable percentage", "delivery pct")
        date_col = _find_column(header, "trade date", "trade dt", "date", "report date")
        if None in (symbol_col, trade_col, delivery_col, pct_col):
            continue
        for row in rows[index + 1:]:
            if max(symbol_col, trade_col, delivery_col, pct_col) >= len(row):
                continue
            symbol = re.sub(r"[^A-Z0-9&_-]", "", row[symbol_col].upper())
            traded = _amount(row[trade_col])
            deliverable = _amount(row[delivery_col])
            pct = _amount(row[pct_col])
            report_date = _parse_date(row[date_col]) if date_col is not None and date_col < len(row) else filename_date
            if symbol and report_date and traded is not None and deliverable is not None:
                output.append({
                    "report_date": report_date,
                    "symbol": symbol,
                    "traded_quantity": traded,
                    "deliverable_quantity": deliverable,
                    "delivery_pct": pct if pct is not None else (100 * deliverable / traded if traded else None),
                    "source_file": filename,
                })
        if output:
            return output

    # NSE MTO .DAT reports are pipe-delimited; DATA records contain type, serial,
    # symbol, traded quantity, deliverable quantity, and deliverable percentage.
    if filename_date:
        for row in rows:
            if len(row) < 6 or row[0].strip().upper() not in {"D", "DATA"}:
                continue
            symbol = re.sub(r"[^A-Z0-9&_-]", "", row[2].upper())
            traded, deliverable, pct = _amount(row[3]), _amount(row[4]), _amount(row[5])
            if symbol and traded is not None and deliverable is not None:
                output.append({
                    "report_date": filename_date,
                    "symbol": symbol,
                    "traded_quantity": traded,
                    "deliverable_quantity": deliverable,
                    "delivery_pct": pct if pct is not None else (100 * deliverable / traded if traded else None),
                    "source_file": filename,
                })
    if output:
        return output
    raise ValueError("Could not read delivery rows. Use NSE MTO_ddmmyyyy.DAT or a CSV with symbol, date, traded quantity, deliverable quantity, and delivery percentage columns.")


def _event_rows(content: str, filename: str) -> list[dict[str, Any]]:
    rows = _rows(content)
    for index, header in enumerate(rows):
        symbol_col = _find_column(header, "symbol", "security symbol", "ticker")
        event_col = _find_column(header, "event", "subject", "announcement", "description")
        date_col = _find_column(header, "event date", "date", "announcement date", "broadcast date")
        url_col = _find_column(header, "url", "link", "source url", "attachment")
        source_col = _find_column(header, "source", "publisher", "exchange")
        if None in (symbol_col, event_col, date_col):
            continue
        output = []
        for row in rows[index + 1:]:
            if max(symbol_col, event_col, date_col) >= len(row):
                continue
            symbol = re.sub(r"[^A-Z0-9&_-]", "", row[symbol_col].upper())
            event_date = _parse_date(row[date_col])
            event = _cell(row[event_col])
            if not symbol or not event_date or not event:
                continue
            url = row[url_col] if url_col is not None and url_col < len(row) else ""
            source = row[source_col] if source_col is not None and source_col < len(row) else filename
            output.append({
                "symbol": symbol, "event_date": event_date, "event": event,
                "source_url": url if url.startswith("http") else "",
                "source_name": source or filename,
            })
        if output:
            return output
    raise ValueError("Could not read corporate events. Use CSV columns symbol, event, date, and optional url/source.")


def import_feed(kind: str, content: str, filename: str) -> dict[str, Any]:
    """Validate and store a user-selected official or licensed feed report."""
    normalized_kind = str(kind).strip().lower()
    if not content.strip():
        raise ValueError("The selected report is empty.")
    imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with _DB_LOCK, _connect() as connection:
        if normalized_kind == "fii_dii":
            data = _fii_rows(content, filename)
            connection.executemany(
                "INSERT OR REPLACE INTO institutional_flows VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(item["report_date"], item["category"], item["buy_value_cr"], item["sell_value_cr"], item["net_value_cr"], filename, imported_at) for item in data],
            )
        elif normalized_kind == "delivery":
            data = _delivery_rows(content, filename)
            connection.executemany(
                "INSERT OR REPLACE INTO delivery_positions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(item["report_date"], item["symbol"], item["traded_quantity"], item["deliverable_quantity"], item["delivery_pct"], filename, imported_at) for item in data],
            )
        elif normalized_kind == "corporate_events":
            data = _event_rows(content, filename)
            connection.executemany(
                "INSERT OR REPLACE INTO corporate_events VALUES (?, ?, ?, ?, ?, ?)",
                [(item["symbol"], item["event_date"], item["event"], item["source_url"], item["source_name"], imported_at) for item in data],
            )
        else:
            raise ValueError("kind must be fii_dii, delivery, or corporate_events")
    return {"kind": normalized_kind, "records_imported": len(data), "filename": filename, "imported_at": imported_at}


def latest_fii_dii() -> dict[str, Any]:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute("SELECT MAX(report_date) AS report_date FROM institutional_flows").fetchone()
        report_date = row["report_date"] if row else None
        if not report_date:
            return {"status": "provider_required", "reason": "Upload an authorized FII/FPI & DII report CSV to include institutional flows."}
        rows = connection.execute(
            "SELECT category, buy_value_cr, sell_value_cr, net_value_cr FROM institutional_flows WHERE report_date = ?",
            (report_date,),
        ).fetchall()
    by_category = {row["category"]: row for row in rows}
    fii, dii = by_category.get("FII/FPI"), by_category.get("DII")
    is_stale = (date.today() - date.fromisoformat(report_date)).days > 7
    return {
        "status": "stale" if is_stale else "available" if fii and dii else "partial",
        "date": report_date,
        "fii_net_cr": fii["net_value_cr"] if fii else None,
        "dii_net_cr": dii["net_value_cr"] if dii else None,
        "fii_buy_cr": fii["buy_value_cr"] if fii else None,
        "fii_sell_cr": fii["sell_value_cr"] if fii else None,
        "dii_buy_cr": dii["buy_value_cr"] if dii else None,
        "dii_sell_cr": dii["sell_value_cr"] if dii else None,
        "reason": "Imported report is older than seven days; refresh it." if is_stale else "User-imported report; daily institutional cash-market flows are aggregate values, not stock-specific activity.",
    }


def latest_delivery(symbols: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    normalized = list(dict.fromkeys(re.sub(r"[^A-Z0-9&_-]", "", symbol.upper()) for symbol in symbols))
    if not normalized:
        return {}, {"status": "provider_required", "symbols_with_data": 0, "symbols_checked": 0, "reason": "Upload an authorized NSE delivery report to include deliverable volume."}
    with _DB_LOCK, _connect() as connection:
        latest = connection.execute("SELECT MAX(report_date) AS report_date FROM delivery_positions").fetchone()["report_date"]
        if not latest:
            return {}, {"status": "provider_required", "symbols_with_data": 0, "symbols_checked": len(normalized), "reason": "Upload an authorized NSE delivery report to include deliverable volume."}
        placeholders = ",".join("?" for _ in normalized)
        rows = connection.execute(
            f"SELECT * FROM delivery_positions WHERE report_date = ? AND symbol IN ({placeholders})",
            [latest, *normalized],
        ).fetchall()
        history = {
            symbol: connection.execute(
                "SELECT report_date, delivery_pct FROM delivery_positions WHERE symbol = ? ORDER BY report_date DESC LIMIT 5",
                (symbol,),
            ).fetchall()
            for symbol in normalized
        }
    today = date.today()
    latest_day = date.fromisoformat(latest)
    is_stale = (today - latest_day).days > 7
    by_symbol = {}
    for row in rows:
        values = [item["delivery_pct"] for item in history.get(row["symbol"], []) if item["delivery_pct"] is not None]
        by_symbol[row["symbol"]] = {
            "status": "stale" if is_stale else "available",
            "date": row["report_date"],
            "traded_quantity": row["traded_quantity"],
            "deliverable_quantity": row["deliverable_quantity"],
            "delivery_pct": row["delivery_pct"],
            "five_session_average_pct": sum(values) / len(values) if values else None,
            "source": "User-imported end-of-day delivery report",
        }
    return by_symbol, {
        "status": "stale" if is_stale else "available" if rows else "partial",
        "date": latest,
        "symbols_with_data": len(by_symbol),
        "symbols_checked": len(normalized),
        "reason": "End-of-day deliverable quantity/percentage; the five-session average uses uploaded dates only." if rows else "Latest uploaded delivery report does not cover the shortlisted symbols.",
    }


def recent_corporate_events(symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    normalized = list(dict.fromkeys(re.sub(r"[^A-Z0-9&_-]", "", symbol.upper()) for symbol in symbols))
    if not normalized:
        return {}
    today = date.today()
    from_day = (today - timedelta(days=14)).isoformat()
    to_day = (today + timedelta(days=30)).isoformat()
    placeholders = ",".join("?" for _ in normalized)
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM corporate_events WHERE symbol IN ({placeholders}) AND event_date BETWEEN ? AND ? ORDER BY event_date DESC",
            [*normalized, from_day, to_day],
        ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["symbol"], []).append({
            "event": row["event"], "date": row["event_date"], "source": row["source_name"],
            "url": row["source_url"],
        })
    return result
