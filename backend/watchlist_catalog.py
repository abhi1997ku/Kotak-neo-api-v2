"""Nifty 50 constituents, refreshed from Nifty Indices with a local fallback."""

from __future__ import annotations

import csv
import io
import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

NIFTY50_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
NIFTY50_FALLBACK = Path(__file__).resolve().parent / "data" / "nifty50_constituents.csv"
_CACHE_SECONDS = 6 * 60 * 60
_cache_lock = threading.Lock()
_constituent_cache: list[dict[str, str]] = []
_constituent_cache_at = 0.0


def _parse_constituents(csv_text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    for row in reader:
        symbol = (row.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": (row.get("Company Name") or symbol).strip(),
                "isin": (row.get("ISIN Code") or "").strip().upper(),
            }
        )
    return rows


def get_nifty50_constituents() -> list[dict[str, str]]:
    """Return current constituents, falling back to the bundled official snapshot."""
    global _constituent_cache, _constituent_cache_at

    now = time.monotonic()
    if _constituent_cache and now - _constituent_cache_at < _CACHE_SECONDS:
        return list(_constituent_cache)

    with _cache_lock:
        now = time.monotonic()
        if _constituent_cache and now - _constituent_cache_at < _CACHE_SECONDS:
            return list(_constituent_cache)

        try:
            response = requests.get(NIFTY50_CSV_URL, timeout=5)
            response.raise_for_status()
            constituents = _parse_constituents(response.text)
            if len(constituents) < 40:
                raise ValueError("Nifty Indices returned an incomplete Nifty 50 constituent list")
            _constituent_cache = constituents
            _constituent_cache_at = time.monotonic()
            return list(constituents)
        except (requests.RequestException, ValueError, csv.Error) as exc:
            logger.warning("Could not refresh Nifty 50 constituents; using bundled snapshot: %s", exc)

        try:
            constituents = _parse_constituents(NIFTY50_FALLBACK.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, csv.Error) as exc:
            logger.exception("Could not load bundled Nifty 50 constituent list")
            raise RuntimeError("Nifty 50 constituent list is unavailable") from exc

        if not constituents:
            raise RuntimeError("Bundled Nifty 50 constituent list is empty")
        _constituent_cache = constituents
        _constituent_cache_at = time.monotonic()
        return list(constituents)


def _row_values(row: dict[str, Any]) -> set[str]:
    values = set()
    for field in ("pSymbolName", "pTrdSymbol", "pCombinedSymbol", "pDesc"):
        value = str(row.get(field) or "").strip().upper()
        if value:
            values.add(value)
            values.add(value.removesuffix("-EQ"))
    return values


def match_nifty50_tokens(
    constituents: list[dict[str, str]], master_rows: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Match official Nifty symbols to Kotak's current NSE cash-market tokens."""
    symbols = {item["symbol"] for item in constituents}
    symbol_by_isin = {
        str(item.get("isin") or "").strip().upper(): item["symbol"]
        for item in constituents
        if str(item.get("isin") or "").strip()
    }
    matches: dict[str, dict[str, str]] = {}

    for row in master_rows:
        values = _row_values(row)
        candidates = values.intersection(symbols)
        isin_match = symbol_by_isin.get(str(row.get("pISIN") or "").strip().upper())
        if isin_match:
            candidates.add(isin_match)
        token = str(row.get("pSymbol") or "").strip()
        if not candidates or not token:
            continue

        trading_symbol = str(row.get("pTrdSymbol") or "").strip().upper()
        # The cash master can also contain non-equity instruments. Prefer only
        # the EQ row when the broker identifies the instrument by trading symbol.
        if trading_symbol and not trading_symbol.endswith("-EQ"):
            continue

        for symbol in candidates:
            current = matches.get(symbol)
            if current is None or trading_symbol.endswith("-EQ"):
                resolved_trading_symbol = (
                    trading_symbol
                    if trading_symbol.removesuffix("-EQ") == symbol
                    else f"{symbol}-EQ"
                )
                matches[symbol] = {
                    "instrument_token": token,
                    "exchange_segment": "nse_cm",
                    "trading_symbol": resolved_trading_symbol,
                }

    return matches


INDEX_DEFINITIONS = [
    {
        "symbol": "NIFTY 50",
        "name": "Nifty 50",
        "exchange_segment": "nse_cm",
        "aliases": {"NIFTY 50", "NIFTY50"},
    },
    {
        "symbol": "NIFTY BANK",
        "name": "Nifty Bank",
        "exchange_segment": "nse_cm",
        "aliases": {"NIFTY BANK", "NIFTYBANK", "BANKNIFTY"},
    },
    {
        "symbol": "SENSEX",
        "name": "Sensex",
        "exchange_segment": "bse_cm",
        "aliases": {"SENSEX"},
    },
]


def match_index_tokens(master_rows_by_segment: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    """Resolve benchmark index tokens from Kotak's NSE and BSE cash masters."""
    matches: list[dict[str, str]] = []
    for definition in INDEX_DEFINITIONS:
        segment_rows = master_rows_by_segment.get(definition["exchange_segment"], [])
        aliases = definition["aliases"]
        matched_row = next(
            (
                row
                for row in segment_rows
                if _row_values(row).intersection(aliases) and str(row.get("pSymbol") or "").strip()
            ),
            None,
        )
        matches.append(
            {
                "symbol": definition["symbol"],
                "name": definition["name"],
                "exchange_segment": definition["exchange_segment"],
                "instrument_token": str(matched_row.get("pSymbol") or "").strip() if matched_row else "",
            }
        )
    return matches


def constituents_source() -> str:
    return "Nifty Indices"
