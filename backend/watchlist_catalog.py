"""Nifty 50/500 constituents refreshed from NSE with local fallbacks."""

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

INDEX_SOURCES = {
    "nifty50": {
        "url": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
        "fallback": Path(__file__).resolve().parent / "data" / "nifty50_constituents.csv",
        "minimum": 40,
        "label": "Nifty 50",
    },
    "nifty500": {
        "url": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
        "fallback": Path(__file__).resolve().parent / "data" / "nifty500_constituents.csv",
        "minimum": 450,
        "label": "Nifty 500",
    },
}
_CACHE_SECONDS = 6 * 60 * 60
_cache_lock = threading.Lock()
_constituent_cache: dict[str, list[dict[str, str]]] = {}
_constituent_cache_at: dict[str, float] = {}
_constituent_sources: dict[str, str] = {}


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
                "industry": (row.get("Industry") or "Unknown").strip(),
                "isin": (row.get("ISIN Code") or "").strip().upper(),
            }
        )
    return rows


def get_index_constituents(index: str = "nifty50") -> list[dict[str, str]]:
    """Return the selected official index membership, with bundled fallback."""
    key = str(index or "nifty50").strip().lower().replace(" ", "")
    if key not in INDEX_SOURCES:
        raise ValueError("index must be 'nifty50' or 'nifty500'")
    config = INDEX_SOURCES[key]
    now = time.monotonic()
    if key in _constituent_cache and now - _constituent_cache_at[key] < _CACHE_SECONDS:
        return list(_constituent_cache[key])

    with _cache_lock:
        now = time.monotonic()
        if key in _constituent_cache and now - _constituent_cache_at[key] < _CACHE_SECONDS:
            return list(_constituent_cache[key])

        try:
            response = requests.get(config["url"], timeout=8)
            response.raise_for_status()
            constituents = _parse_constituents(response.text)
            if len(constituents) < config["minimum"]:
                raise ValueError(f"NSE returned an incomplete {config['label']} constituent list")
            _constituent_cache[key] = constituents
            _constituent_cache_at[key] = time.monotonic()
            _constituent_sources[key] = "NSE/Nifty Indices live constituent file"
            return list(constituents)
        except (requests.RequestException, ValueError, csv.Error) as exc:
            logger.warning("Could not refresh %s constituents; using bundled snapshot: %s", config["label"], exc)

        try:
            constituents = _parse_constituents(config["fallback"].read_text(encoding="utf-8-sig"))
            if len(constituents) < config["minimum"]:
                raise ValueError(f"Bundled {config['label']} constituent list is incomplete")
        except (OSError, ValueError, csv.Error) as exc:
            logger.exception("Could not load bundled %s constituent list", config["label"])
            raise RuntimeError(f"{config['label']} constituent list is unavailable") from exc

        if not constituents:
            raise RuntimeError(f"Bundled {config['label']} constituent list is empty")
        _constituent_cache[key] = constituents
        _constituent_cache_at[key] = time.monotonic()
        _constituent_sources[key] = f"Bundled {config['label']} constituent snapshot"
        return list(constituents)


def get_nifty50_constituents() -> list[dict[str, str]]:
    return get_index_constituents("nifty50")


def get_nifty500_constituents() -> list[dict[str, str]]:
    return get_index_constituents("nifty500")


def _row_values(row: dict[str, Any]) -> set[str]:
    values = set()
    for field in ("pSymbolName", "pTrdSymbol", "pCombinedSymbol", "pDesc"):
        value = str(row.get(field) or "").strip().upper()
        if value:
            values.add(value)
            values.add(value.removesuffix("-EQ"))
    return values


def match_constituent_tokens(
    constituents: list[dict[str, str]], master_rows: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Match official NSE constituent symbols to Kotak's NSE cash-market tokens."""
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


def match_nifty50_tokens(
    constituents: list[dict[str, str]], master_rows: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Backward-compatible alias; matching works for any NSE constituent set."""
    return match_constituent_tokens(constituents, master_rows)


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


def constituents_source(index: str = "nifty50") -> str:
    key = str(index or "nifty50").strip().lower().replace(" ", "")
    return _constituent_sources.get(key, "NSE/Nifty Indices")
