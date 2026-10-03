from __future__ import annotations

import os
import sys
import time
import logging
import csv
import io
import asyncio
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from dotenv import load_dotenv
import requests

# Add parent directory to path so we can import neo_api_client
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neo_api_client import NeoAPI
from neo_api_client.api.scrip_master_api import ScripMasterAPI
from neo_api_client.urls import SFEED_WEBSOCKET_URL
from neo_api_client.websocket.feed import SFeedIndex, SFeedMarketStatus, SFeedScrip, SFeedScripLite, SFeedWebSocket, WsToken
from backend.watchlist_catalog import (
    constituents_source,
    get_nifty50_constituents,
    match_index_tokens,
    match_nifty50_tokens,
)

logger = logging.getLogger(__name__)


class KotakClientError(RuntimeError):
    """Raised when the Kotak client cannot complete a broker request."""


def _first_value(record: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    """Parse a numeric quote field without turning a missing field into zero."""
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _integer(value: Any, default: int = 0) -> int:
    return int(_number(value, default))


def _optional_integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return _integer(value)


def _quote_value(record: dict[str, Any], *names: str, default: Any = "") -> Any:
    value = _first_value(record, *names, default=None)
    if value is not None:
        return value
    depth = record.get("depth") or record.get("marketDepth") or {}
    if isinstance(depth, dict):
        for side, side_names in (("buy", ("bid", "buyPrice", "price", "bp1", "bp")), ("sell", ("ask", "sellPrice", "price", "sp1", "sp"))):
            if any(name in names for name in side_names):
                levels = depth.get(side) or depth.get(side.capitalize()) or []
                if isinstance(levels, list) and levels and isinstance(levels[0], dict):
                    return _first_value(levels[0], *side_names, default=default)
    return default


def _quote_token(record: dict[str, Any]) -> str:
    token = str(_first_value(record, "instrument_token", "instrumentToken", "token", "exchange_token", "exchangeToken", "tk", "symbolToken"))
    return token.split("|")[-1]


def _quote_records(payload: Any) -> list[dict[str, Any]]:
    """Normalize both list-shaped and token-keyed quote responses."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "result", "results", "quotes"):
        nested = payload.get(key)
        if nested is not None:
            records = _quote_records(nested)
            if records:
                return records
    if payload and all(isinstance(value, dict) for value in payload.values()):
        records = []
        for token, quote in payload.items():
            item = dict(quote)
            item.setdefault("tk", token)
            records.append(item)
        return records
    return [payload]


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "result", "results", "positions", "holdings", "orders", "trades", "limits"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
            if isinstance(nested, dict):
                return [nested]
        return [payload]
    return []


def _expiry_date(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip()
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.upper(), fmt)
        except ValueError:
            continue
    return None


def _resolve_expiry(expiry: str | None) -> str:
    if expiry not in {"next_week", "next_month"}:
        return expiry or ""
    today = datetime.now()
    days_until_thursday = (3 - today.weekday()) % 7 or 7
    target = today.replace(hour=0, minute=0, second=0, microsecond=0)
    target = target + timedelta(days=days_until_thursday)
    if expiry == "next_month":
        month = target.month % 12 + 1
        year = target.year + (1 if target.month == 12 else 0)
        target = target.replace(year=year, month=month, day=1)
        target = target + timedelta(days=(3 - target.weekday()) % 7)
    return target.strftime("%d%b%Y").upper()


@dataclass
class KotakSession:
    view_token: Optional[str] = None
    trade_token: Optional[str] = None
    sid: Optional[str] = None
    edit_sid: Optional[str] = None
    base_url: Optional[str] = None
    data_center: Optional[str] = None
    feed_url: Optional[str] = None
    expires_at: Optional[float] = None


class KotakClient:
    """Thin wrapper around the Kotak Neo Python SDK / REST API.

    This implementation uses the real local SDK package in this repository. The
    methods below are still kept in a clean wrapper form so the FastAPI routes can
    remain straightforward while the project grows.
    """

    def __init__(self, env_file: str | Path | None = None) -> None:
        # Look for .env in repo root (parent of backend directory)
        if env_file:
            default_env = Path(env_file)
        else:
            default_env = Path(__file__).resolve().parent.parent / ".env"
        self.env_file = default_env
        self.session = KotakSession()
        self._scrip_master_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._scrip_master_warnings: dict[str, str] = {}
        self.config = self._load_config()
        self.client = self._build_client()

    def _load_config(self) -> Dict[str, Any]:
        load_dotenv(self.env_file)
        return {
            "consumer_key": os.getenv("KOTAK_CONSUMER_KEY"),
            "mobile": os.getenv("KOTAK_MOBILE"),
            "ucc": os.getenv("KOTAK_UCC"),
            "mpin": os.getenv("KOTAK_MPIN"),
            "environment": (os.getenv("KOTAK_ENVIRONMENT") or "prod").lower(),
            "totp_secret": os.getenv("TOTP_SECRET"),
        }

    def _build_client(self) -> NeoAPI:
        if not self.config["consumer_key"]:
            raise KotakClientError("KOTAK_CONSUMER_KEY is missing from the environment.")
        return NeoAPI(
            environment=self.config["environment"],
            access_token=None,
            neo_fin_key=None,
            consumer_key=self.config["consumer_key"],
        )

    def _raise_for_broker_error(self, response: Dict[str, Any], action: str) -> None:
        """Translate a broker error payload into a readable `KotakClientError`."""
        if not isinstance(response, dict):
            return

        errors = response.get("error")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                message = first.get("message") or first.get("error") or str(first)
                if message:
                    raise KotakClientError(f"{action}: {message}")
        if "message" in response and response["message"]:
            raise KotakClientError(f"{action}: {response['message']}")
        if "Error" in response and response["Error"]:
            raise KotakClientError(f"{action}: {response['Error']}")
        if "detail" in response and response["detail"]:
            raise KotakClientError(f"{action}: {response['detail']}")

    def ensure_logged_in(self) -> None:
        """Use the existing session if valid, otherwise trigger a re-login prompt."""
        configuration = getattr(self.client, "configuration", None)
        if configuration and getattr(configuration, "edit_token", None) and getattr(configuration, "edit_sid", None):
            return
        raise KotakClientError("Kotak session is not active. Prompt a fresh login or TOTP validation.")

    def login(self, totp: str | None = None) -> Dict[str, Any]:
        """Authenticate by generating the view token from TOTP, then validate MPIN if provided."""
        if not self.config["mobile"] or not self.config["ucc"]:
            raise KotakClientError("KOTAK_MOBILE and KOTAK_UCC must be set.")
        if not totp:
            raise KotakClientError("TOTP is required. Pass a current 6-digit code.")

        response = self.client.totp_login(
            mobile_number=self.config["mobile"],
            ucc=self.config["ucc"],
            totp=totp,
        )
        if not isinstance(response, dict):
            raise KotakClientError("TOTP login returned an invalid broker response.")
        if response.get("error"):
            self._raise_for_broker_error(response, "Invalid TOTP")
        data = response.get("data") or {}
        self.session.view_token = data.get("token")
        self.session.sid = data.get("sid")
        if not self.session.view_token or not self.session.sid:
            raise KotakClientError("TOTP login did not return a valid broker session.")
        return response

    def login_with_mpin(self, mpin: str | None = None) -> Dict[str, Any]:
        """Complete the second factor and create a valid trade session."""
        if not mpin:
            raise KotakClientError("MPIN is required.")
        response = self.client.totp_validate(mpin=mpin)
        if not isinstance(response, dict):
            raise KotakClientError("MPIN validation returned an invalid broker response.")
        if response.get("error"):
            self._raise_for_broker_error(response, "MPIN validation failed")
        data = response.get("data") or {}
        self.session.trade_token = data.get("token")
        self.session.edit_sid = data.get("sid")
        self.session.base_url = data.get("baseUrl")
        self.session.data_center = data.get("dataCenter")
        self.session.feed_url = data.get("feedUrl")
        if not self.session.trade_token or not self.session.edit_sid:
            raise KotakClientError("MPIN validation did not return a valid trade session.")
        return response

    def refresh_session_if_needed(self) -> None:
        """Check for auth expiry and prompt re-login if needed."""
        try:
            self.ensure_logged_in()
        except KotakClientError:
            raise

    def get_quotes(
        self,
        symbols: List[str],
        exchange_segment: str = "nse_cm",
        quote_type: str = "ltp",
    ) -> Dict[str, Any]:
        """Fetch live quotes for a list of symbols when the broker_session is valid."""
        self.ensure_logged_in()
        try:
            quotes = []
            # Keep batches at 25: Kotak's quote endpoint currently rejects a
            # 50-symbol request with "Please set the Neo symbol max value to 50."
            for offset in range(0, len(symbols), 25):
                if offset:
                    time.sleep(0.04)
                payload = [
                    {"instrument_token": token, "exchange_segment": exchange_segment}
                    for token in symbols[offset : offset + 25]
                ]
                result = self.client.quotes(instrument_tokens=payload, quote_type=quote_type)
                if isinstance(result, dict) and (result.get("error") or result.get("Error")):
                    self._raise_for_broker_error(result, "Quote fetch failed")
                if isinstance(result, dict) and result.get("fault"):
                    raise KotakClientError(f"Quote fetch returned a broker fault: {result['fault']}")
                for quote in _quote_records(result):
                    ltp = _number(_quote_value(quote, "ltp", "lastPrice", "lastTradedPrice", "last_traded_price", "lp", "iv", "price"))
                    raw_change = _optional_number(
                        _first_value(quote, "cng", "change", "netChange", "net_change", "dayChange", default=None)
                    )
                    raw_change_pct = _optional_number(
                        _first_value(
                            quote,
                            "nc",
                            "per_change",
                            "changePct",
                            "netChangePercentage",
                            "net_change_percent",
                            "dayChangePercentage",
                            default=None,
                        )
                    )
                    ohlc = quote.get("ohlc") or quote.get("OHLC") or {}
                    close_value = _first_value(ohlc, "close", "c", default=None) if isinstance(ohlc, dict) else None
                    if close_value in (None, ""):
                        close_value = _first_value(quote, "close", "closePrice", "closingPrice", default=None)
                    close = _optional_number(close_value)
                    if raw_change is None and close is not None:
                        raw_change = ltp - close
                    if raw_change_pct is None and close not in (None, 0) and raw_change is not None:
                        raw_change_pct = raw_change / close * 100
                    quotes.append({
                        "symbol": _first_value(quote, "ts", "tradingSymbol", "display_symbol", "symbol", default=_quote_token(quote)),
                        "instrument_token": _quote_token(quote),
                        "exchange_segment": _first_value(quote, "exchange", "exchange_segment", "e", default=exchange_segment),
                        "ltp": ltp,
                        "bid": _number(_quote_value(quote, "bid", "bestBid", "best_bid", "buyPrice", "buy_price", "bp1", "bp")),
                        "ask": _number(_quote_value(quote, "ask", "bestAsk", "best_ask", "sellPrice", "sell_price", "sp1", "sp")),
                        "volume": _integer(_quote_value(quote, "volume", "tradedVolume", "volume_traded", "last_volume", "v")),
                        "change": raw_change,
                        "change_pct": raw_change_pct,
                    })
            return {"quotes": quotes}
        except Exception as exc:
            raise KotakClientError(f"Quote fetch failed: {exc}") from exc

    def _load_scrip_master(self, exchange_segment: str) -> list[dict[str, Any]]:
        """Download Kotak's instrument master, falling back to the SDK's local copy."""
        now = time.monotonic()
        cached = self._scrip_master_cache.get(exchange_segment)
        cache_ttl = 60 if exchange_segment in self._scrip_master_warnings else 6 * 60 * 60
        if cached and now - cached[0] < cache_ttl:
            return cached[1]

        master_error = ""
        try:
            master_url = ScripMasterAPI(self.client.api_client).scrip_master_init(
                exchange_segment=exchange_segment
            )
            if not isinstance(master_url, str) or not master_url.startswith("https://"):
                if isinstance(master_url, dict):
                    code = _first_value(master_url, "code", "stCode", "statusCode", default="")
                    message = _first_value(master_url, "message", "error", "Error", "errMsg", default="")
                    detail = f"Kotak returned {code}: {message}".strip() if code or message else "Kotak returned an error response"
                else:
                    detail = "Kotak returned no downloadable file URL"
                raise KotakClientError(f"{exchange_segment} scrip master unavailable: {detail}.")

            response = requests.get(master_url, timeout=20)
            response.raise_for_status()
            reader = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
            rows = [
                {str(key).strip(): value.strip() if isinstance(value, str) else value for key, value in row.items() if key}
                for row in reader
            ]
            if not rows:
                raise KotakClientError(f"Kotak's {exchange_segment} scrip master was empty.")
            self._scrip_master_cache[exchange_segment] = (time.monotonic(), rows)
            self._scrip_master_warnings.pop(exchange_segment, None)
            return rows
        except Exception as exc:
            master_error = str(exc) or type(exc).__name__

        local_master = Path(__file__).resolve().parent.parent / "neo_api_client" / "api" / f"{exchange_segment}.csv"
        try:
            local_stat = local_master.stat()
            with local_master.open("r", encoding="utf-8-sig", newline="") as source:
                rows = [
                    {str(key).strip(): value.strip() if isinstance(value, str) else value for key, value in row.items() if key}
                    for row in csv.DictReader(source)
                ]
            if not rows:
                raise ValueError("local instrument file is empty")
        except (OSError, csv.Error, ValueError) as exc:
            detail = master_error[:240] if master_error else "Kotak returned an error response"
            raise KotakClientError(
                f"{exchange_segment} scrip master unavailable: {detail}; local token file could not be used ({exc})."
            ) from exc

        cache_date = datetime.fromtimestamp(local_stat.st_mtime).astimezone().date().isoformat()
        self._scrip_master_warnings[exchange_segment] = (
            f"Kotak's live {exchange_segment} instrument master is unavailable ({master_error[:180]}). "
            f"Using the SDK's cached Kotak instrument file dated {cache_date}."
        )
        self._scrip_master_cache[exchange_segment] = (time.monotonic(), rows)
        logger.warning("Using cached Kotak %s instrument file dated %s", exchange_segment, cache_date)
        return rows

    def _watchlist_catalog(self) -> Dict[str, Any]:
        """Build watchlist metadata and resolve Kotak instrument tokens."""
        constituents = get_nifty50_constituents()
        master_rows: dict[str, list[dict[str, Any]]] = {}
        master_errors = []
        master_warnings = []
        # Stocks in this watchlist are NSE listings. Indices, including Sensex,
        # use Kotak's SFeed index subscription below and don't need a BSE cash
        # scrip-master download just to receive live ticks.
        for segment in ("nse_cm",):
            try:
                master_rows[segment] = self._load_scrip_master(segment)
                cached_warning = self._scrip_master_warnings.get(segment)
                if cached_warning:
                    master_warnings.append(cached_warning)
            except Exception as exc:
                master_rows[segment] = []
                message = str(exc) if isinstance(exc, KotakClientError) else type(exc).__name__
                logger.warning("Watchlist %s instrument master unavailable: %s", segment, message[:240])
                master_errors.append(f"{segment}: {message}")

        nse_master = master_rows["nse_cm"]
        stock_tokens = match_nifty50_tokens(constituents, nse_master)
        stocks = []
        for constituent in constituents:
            token_data = stock_tokens.get(constituent["symbol"], {})
            stocks.append(
                {
                    **{key: value for key, value in constituent.items() if key != "isin"},
                    "trading_symbol": token_data.get("trading_symbol", f"{constituent['symbol']}-EQ"),
                    "instrument_token": token_data.get("instrument_token", ""),
                    "exchange_segment": token_data.get("exchange_segment", "nse_cm"),
                    "ltp": None,
                    "change": None,
                    "change_pct": None,
                }
            )

        missing_symbols = [stock["symbol"] for stock in stocks if not stock["instrument_token"]]
        warning_parts = list(master_warnings)
        if master_warnings and missing_symbols:
            warning_parts.append(
                f"Live prices are available for {len(stocks) - len(missing_symbols)} of {len(stocks)} Nifty 50 stocks; "
                f"the cached file has no token for {', '.join(missing_symbols)}."
            )
        elif master_warnings:
            warning_parts.append(f"Live prices are available for all {len(stocks)} Nifty 50 stocks.")
        if master_errors:
            warning_parts.append("Some exchange/index tokens could not be refreshed: " + "; ".join(master_errors))
        warning = " ".join(warning_parts) or None

        stock_token_count = len(stocks) - len(missing_symbols)
        indices = match_index_tokens(master_rows)
        # Kotak's quote API accepts these index names directly. Use the same
        # identifiers as SFeed so index snapshots work without index rows in
        # the cash-market master.
        index_quote_tokens = {
            "NIFTY 50": "Nifty 50",
            "NIFTY BANK": "Nifty Bank",
            "SENSEX": "SENSEX",
        }
        for index in indices:
            index["instrument_token"] = index_quote_tokens.get(
                index["symbol"], index["instrument_token"]
            )

        return {
            "indices": indices,
            "stocks": stocks,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "constituents_source": constituents_source(),
            "constituent_count": len(stocks),
            "stock_token_count": stock_token_count,
            "stocks_without_token": missing_symbols,
            "catalog_warning": warning,
        }

    def get_watchlist(self) -> Dict[str, Any]:
        """Return an initial quote snapshot; subsequent updates use SFeed ticks."""
        self.ensure_logged_in()
        try:
            watchlist = self._watchlist_catalog()
            quote_batches: dict[str, list[str]] = {"nse_cm": [], "bse_cm": []}
            for stock in watchlist["stocks"]:
                if stock["instrument_token"]:
                    quote_batches["nse_cm"].append(stock["instrument_token"])
            for index in watchlist["indices"]:
                if index["instrument_token"]:
                    quote_batches[index["exchange_segment"]].append(index["instrument_token"])

            quote_by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for segment, tokens in quote_batches.items():
                if not tokens:
                    continue
                # The LTP-only response omits day change and percentage fields.
                # Fetch the full quote snapshot so the watchlist can show them
                # immediately, including outside live-feed market hours.
                batch_quotes = self.get_quotes(tokens, exchange_segment=segment, quote_type="all")["quotes"]
                for quote in batch_quotes:
                    quote_by_key[(segment, quote["instrument_token"])] = quote

            for stock in watchlist["stocks"]:
                quote = quote_by_key.get(("nse_cm", stock["instrument_token"]))
                if quote:
                    stock.update({key: quote[key] for key in ("ltp", "change", "change_pct")})
            for index in watchlist["indices"]:
                quote = quote_by_key.get((index["exchange_segment"], index["instrument_token"]))
                if quote:
                    index.update({key: quote[key] for key in ("ltp", "change", "change_pct")})
                else:
                    index.update({"ltp": None, "change": None, "change_pct": None})

            watchlist["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            return watchlist
        except KotakClientError:
            raise
        except Exception as exc:
            raise KotakClientError(f"Watchlist quote fetch failed: {exc}") from exc

    async def watchlist_tick_stream(self) -> AsyncIterator[dict[str, Any]]:
        """Yield live Kotak SFeed price and market-status events for the watchlist."""
        self.ensure_logged_in()
        configuration = self.client.configuration
        access_token = getattr(configuration, "edit_token", None) or self.session.trade_token
        sid = getattr(configuration, "edit_sid", None) or self.session.edit_sid
        if not self.config.get("ucc") or not access_token or not sid:
            raise KotakClientError("Kotak session is missing credentials required by the live market feed.")

        try:
            catalog = await asyncio.to_thread(self._watchlist_catalog)
        except Exception as exc:
            raise KotakClientError(f"Could not prepare live watchlist subscriptions: {exc}") from exc

        stock_by_token = {
            (stock["exchange_segment"], stock["instrument_token"]): stock["symbol"]
            for stock in catalog["stocks"]
            if stock["instrument_token"]
        }
        index_symbols = {
            "NIFTY50": "NIFTY 50",
            "NIFTYBANK": "NIFTY BANK",
            "BANKNIFTY": "NIFTY BANK",
            "SENSEX": "SENSEX",
        }
        stock_tokens = [WsToken(segment, token) for segment, token in stock_by_token]
        index_tokens = [
            WsToken("nse_cm", "Nifty 50"),
            WsToken("nse_cm", "Nifty Bank"),
            WsToken("bse_cm", "SENSEX"),
        ]

        status_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        feed = SFeedWebSocket(
            access_token=access_token,
            sid=sid,
            ucc=self.config["ucc"],
            url=self.session.feed_url or SFEED_WEBSOCKET_URL,
        )
        stream_state = "partial" if catalog.get("stock_token_count", 0) < catalog.get("constituent_count", 0) else "live"
        feed.on_connect = lambda: status_events.put_nowait({"type": "status", "state": stream_state})
        feed.on_disconnect = lambda: status_events.put_nowait({"type": "status", "state": "reconnecting"})
        feed.on_error = lambda error: logger.warning("Kotak SFeed error: %s", type(error).__name__)

        async with feed:
            if stock_tokens:
                await feed.subscribe_scrips_lite(stock_tokens)
            await feed.subscribe_index(index_tokens)
            await feed.subscribe_exchange()
            yield {
                "type": "status",
                "state": stream_state,
                "message": catalog.get("catalog_warning"),
            }

            next_message = asyncio.create_task(feed.__anext__())
            next_status = asyncio.create_task(status_events.get())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {next_message, next_status},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if next_status in done:
                        yield next_status.result()
                        next_status = asyncio.create_task(status_events.get())
                    if next_message in done:
                        try:
                            message = next_message.result()
                        except StopAsyncIteration:
                            break
                        next_message = asyncio.create_task(feed.__anext__())

                        if isinstance(message, SFeedScripLite):
                            symbol = stock_by_token.get((message.exchange_segment, message.instrument_token))
                            if symbol:
                                yield {
                                    "type": "quote",
                                    "symbol": symbol,
                                    "ltp": message.last_traded_price,
                                    "change": message.net_change,
                                    "change_pct": message.net_change_percent,
                                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                }
                        elif isinstance(message, SFeedIndex):
                            normalized_name = "".join(character for character in message.name.upper() if character.isalnum())
                            symbol = index_symbols.get(normalized_name)
                            if symbol:
                                yield {
                                    "type": "quote",
                                    "symbol": symbol,
                                    "ltp": message.last_traded_price,
                                    "change": message.change,
                                    "change_pct": message.net_change_percent,
                                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                }
                        elif isinstance(message, SFeedMarketStatus):
                            yield {
                                "type": "market_status",
                                "exchange_segment": message.exchange_segment,
                                "status": message.status,
                                "status_code": message.status_code,
                            }
            finally:
                for task in (next_message, next_status):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(next_message, next_status, return_exceptions=True)

    async def option_chain_tick_stream(
        self, tokens: list[dict[str, str]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield live option ticks for the currently displayed chain."""
        self.ensure_logged_in()
        configuration = self.client.configuration
        access_token = getattr(configuration, "edit_token", None) or self.session.trade_token
        sid = getattr(configuration, "edit_sid", None) or self.session.edit_sid
        if not self.config.get("ucc") or not access_token or not sid:
            raise KotakClientError("Kotak session is missing credentials required by the live market feed.")

        ws_tokens = [
            WsToken(token["exchange_segment"], token["instrument_token"])
            for token in tokens
        ]
        if not ws_tokens:
            raise KotakClientError("No option tokens were provided for the live price feed.")

        feed = SFeedWebSocket(
            access_token=access_token,
            sid=sid,
            ucc=self.config["ucc"],
            url=self.session.feed_url or SFEED_WEBSOCKET_URL,
        )
        async with feed:
            await feed.subscribe_scrips_lite(ws_tokens)
            yield {"type": "status", "state": "connected"}
            async for message in feed:
                if isinstance(message, SFeedScripLite):
                    yield {
                        "type": "quote",
                        "exchange_segment": message.exchange_segment,
                        "instrument_token": message.instrument_token,
                        "ltp": message.last_traded_price,
                        "change": message.net_change,
                        "change_pct": message.net_change_percent,
                        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }

    async def position_tick_stream(
        self, tokens: list[dict[str, str]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield initial and streaming LTP updates for open position instruments."""
        self.ensure_logged_in()
        configuration = self.client.configuration
        access_token = getattr(configuration, "edit_token", None) or self.session.trade_token
        sid = getattr(configuration, "edit_sid", None) or self.session.edit_sid
        if not self.config.get("ucc") or not access_token or not sid:
            raise KotakClientError("Kotak session is missing credentials required by the live market feed.")

        ws_tokens = [
            WsToken(token["exchange_segment"], token["instrument_token"])
            for token in tokens
        ]
        symbol_by_token = {
            (token["exchange_segment"], token["instrument_token"]): token["symbol"]
            for token in tokens
        }
        if not ws_tokens:
            raise KotakClientError("No open-position instrument tokens were provided for the live price feed.")

        feed = SFeedWebSocket(
            access_token=access_token,
            sid=sid,
            ucc=self.config["ucc"],
            url=self.session.feed_url or SFEED_WEBSOCKET_URL,
        )
        async with feed:
            yield {"type": "status", "state": "connected"}

            tokens_by_segment: dict[str, list[str]] = {}
            for token in tokens:
                tokens_by_segment.setdefault(token["exchange_segment"], []).append(token["instrument_token"])
            for segment, instrument_tokens in tokens_by_segment.items():
                try:
                    snapshot = await asyncio.to_thread(
                        self.get_quotes,
                        instrument_tokens,
                        segment,
                        "ltp",
                    )
                except KotakClientError as exc:
                    logger.warning("Position LTP snapshot unavailable for %s: %s", segment, str(exc)[:180])
                    continue
                for quote in snapshot.get("quotes", []):
                    key = (segment, str(quote.get("instrument_token", "")))
                    symbol = symbol_by_token.get(key)
                    if symbol and quote.get("ltp", 0) > 0:
                        yield {
                            "type": "quote",
                            "source": "snapshot",
                            "symbol": symbol,
                            "instrument_token": key[1],
                            "exchange_segment": segment,
                            "ltp": quote["ltp"],
                            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        }

            await feed.subscribe_scrips_lite(ws_tokens)
            async for message in feed:
                if isinstance(message, SFeedScripLite):
                    key = (str(message.exchange_segment).lower(), str(message.instrument_token))
                    symbol = symbol_by_token.get(key)
                    if symbol:
                        yield {
                            "type": "quote",
                            "source": "stream",
                            "symbol": symbol,
                            "instrument_token": message.instrument_token,
                            "exchange_segment": message.exchange_segment,
                            "ltp": message.last_traded_price,
                            "change": message.net_change,
                            "change_pct": message.net_change_percent,
                            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        }

    def _nearest_banknifty_future(self) -> dict[str, Any]:
        """Resolve the current BANKNIFTY index future from Kotak's live master."""
        today = datetime.now().date()
        candidates: list[tuple[datetime, dict[str, Any]]] = []
        for row in self._load_scrip_master("nse_fo"):
            name = str(row.get("pSymbolName") or row.get("symbol") or "").upper().replace(" ", "")
            instrument_type = str(row.get("pInstType") or row.get("instrument_type") or "").upper()
            expiry = _expiry_date(row.get("pExpiryDate") or row.get("expiry"))
            token = str(row.get("pSymbol") or row.get("instrument_token") or "").strip()
            if name == "BANKNIFTY" and instrument_type.startswith("FUT") and token and expiry and expiry.date() >= today:
                candidates.append((expiry, row))
        if not candidates:
            raise KotakClientError("Could not resolve a current BANKNIFTY futures contract from Kotak's instrument master.")
        return min(candidates, key=lambda item: item[0])[1]

    async def banknifty_future_tick_stream(self) -> AsyncIterator[dict[str, Any]]:
        """Yield full live ticks for the nearest BANKNIFTY future, including volume."""
        self.ensure_logged_in()
        configuration = self.client.configuration
        access_token = getattr(configuration, "edit_token", None) or self.session.trade_token
        sid = getattr(configuration, "edit_sid", None) or self.session.edit_sid
        if not self.config.get("ucc") or not access_token or not sid:
            raise KotakClientError("Kotak session is missing credentials required by the live market feed.")

        contract = await asyncio.to_thread(self._nearest_banknifty_future)
        token = str(contract.get("pSymbol") or contract.get("instrument_token"))
        trading_symbol = str(contract.get("pTrdSymbol") or contract.get("trading_symbol") or token)
        lot_size = _integer(contract.get("lLotSize") or contract.get("lot_size"), 1)
        feed = SFeedWebSocket(
            access_token=access_token,
            sid=sid,
            ucc=self.config["ucc"],
            url=self.session.feed_url or SFEED_WEBSOCKET_URL,
        )
        async with feed:
            await feed.subscribe_scrips([WsToken("nse_fo", token)])
            yield {"type": "status", "state": "connected", "symbol": trading_symbol, "instrument_token": token, "lot_size": lot_size}
            async for message in feed:
                if isinstance(message, SFeedScrip) and message.instrument_token == token:
                    yield {
                        "type": "quote",
                        "symbol": trading_symbol,
                        "instrument_token": token,
                        "ltp": message.last_traded_price,
                        "volume": message.volume_traded_today,
                        "last_trade_time": message.last_trade_time,
                        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }

    def quote_by_instrument_tokens(self, instrument_tokens: List[Dict[str, Any]], quote_type: str = "ltp") -> Dict[str, Any]:
        self.ensure_logged_in()
        return self.client.quotes(instrument_tokens=instrument_tokens, quote_type=quote_type)

    def place_order(
        self,
        exchange_segment: str,
        product: str,
        order_type: str,
        transaction_type: str,
        quantity: int,
        price: float | None = None,
        amo: str = "NO",
        trading_symbol: str | None = None,
        validity: str = "DAY",
        trigger_price: float | None = None,
        tag: str | None = None,
        scrip_token: str | None = None,
    ) -> Dict[str, Any]:
        """Place an order through the SDK wrapper."""
        self.ensure_logged_in()
        normalized_amo = str(amo or "NO").strip().upper()
        if normalized_amo not in {"YES", "NO"}:
            raise KotakClientError("Order placement failed: AMO must be YES or NO.")
        normalized_side = {"BUY": "B", "SELL": "S", "Buy": "B", "Sell": "S"}.get(transaction_type, transaction_type)
        # Kotak treats `ig` as the client order ID. The UI's tag is a stable
        # order label, so add a per-submission suffix to keep later orders from
        # being rejected as duplicate client order IDs.
        tag_prefix = "".join(character for character in str(tag or "terminal").upper() if character.isalnum())[:3] or "ORD"
        client_order_id = f"{tag_prefix}{uuid.uuid4().hex[:12]}"
        result = self.client.place_order(
            exchange_segment=exchange_segment,
            product=product,
            price=str(price if price is not None else 0),
            order_type=order_type,
            quantity=str(quantity),
            validity=validity,
            trading_symbol=trading_symbol or "",
            transaction_type=normalized_side,
            amo=normalized_amo,
            disclosed_quantity="0",
            market_protection="0",
            pf="N",
            trigger_price=str(trigger_price if trigger_price is not None else 0),
            tag=client_order_id,
            scrip_token=scrip_token,
        )
        if isinstance(result, dict):
            broker_code = str(result.get("stCode", ""))
            if broker_code in {"100008", "1037"}:
                logger.warning(
                    "Kotak order rejected: client_order_id=%r http_status=%r stCode=%r stat=%r errMsg=%r",
                    client_order_id,
                    result.get("_http_status"),
                    result.get("stCode"),
                    result.get("stat"),
                    result.get("errMsg"),
                )
            if broker_code == "100008":
                raise KotakClientError(
                    "Order placement failed: Kotak Neo rejected this server's public IP. "
                    "Add a static IP under Kotak Neo > More > Trade API > your application > Add IP, "
                    "then sign in again from that same network."
                )
            if broker_code == "1037":
                raise KotakClientError(
                    "Order placement failed: Kotak Neo says the API authentication was created from a "
                    "different public IP than the order request. Whitelist the current public IP and "
                    "authenticate again from that same environment."
                )
        if isinstance(result, dict) and (
            result.get("Error")
            or result.get("Error Message")
            or result.get("error")
            or result.get("errMsg")
            or result.get("emsg")
            or result.get("rejRsn")
            or str(result.get("stat", "")).lower() in {"not_ok", "failed", "failure"}
        ):
            broker_error = result.get("error")
            if isinstance(broker_error, list) and broker_error:
                first_error = broker_error[0]
                if isinstance(first_error, dict):
                    broker_error = first_error.get("message") or first_error.get("error")
            elif hasattr(broker_error, "reason"):
                broker_error = (
                    f"{type(broker_error).__name__}(status={getattr(broker_error, 'status', None)}, "
                    f"reason={getattr(broker_error, 'reason', None)})"
                )
            elif broker_error is not None:
                broker_error = repr(broker_error)[:300]
            logger.warning(
                "Kotak order rejected: client_order_id=%r http_status=%r stCode=%r stat=%r errMsg=%r error=%r",
                client_order_id,
                result.get("_http_status"),
                result.get("stCode"),
                result.get("stat"),
                result.get("errMsg") or result.get("message"),
                broker_error,
            )
            error = result.get("Error") or result.get("Error Message") or result.get("error") or result.get("errMsg") or result.get("emsg") or result.get("rejRsn")
            if isinstance(error, str):
                error_text = error
            elif isinstance(error, dict):
                error_text = str(error.get("message") or error.get("error") or error.get("reason") or repr(error))
            else:
                error_text = repr(error)
            if "error from core" in error_text.strip().lower():
                broker_detail = str(result.get("stat") or "").strip()
                if broker_detail and broker_detail.lower() not in {"not_ok", "failed", "failure"}:
                    error_text = broker_detail
            if "login manager is down" in error_text.lower():
                raise KotakClientError(
                    "Order placement failed: Kotak Neo's after-market order service is unavailable right now. "
                    "Kotak rejected the request; no order was placed. Retry AMO later, or place a regular order "
                    "during market hours."
                )
            raise KotakClientError(f"Order placement failed: {error_text}")
        order_record = _records(result)[0] if _records(result) else result if isinstance(result, dict) else {}
        order_id = str(_first_value(order_record, "orderId", "orderID", "order_id", "nOrdNo", default=""))
        status = str(_first_value(order_record, "orderStatus", "status", "ordSt", default="submitted")).upper()
        if not order_id:
            raise KotakClientError("Order placement failed: broker returned no order ID. Check the broker response and order parameters.")
        return {"order_id": order_id, "status": status, "raw": result}

    def modify_order(self, order_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Modify an existing order via the SDK wrapper."""
        self.ensure_logged_in()
        return self.client.modify_order(order_id=order_id, **kwargs)

    def cancel_order(self, order_id: str, amo: str = "NO") -> Dict[str, Any]:
        """Cancel one order via the SDK wrapper."""
        self.ensure_logged_in()
        return self.client.cancel_order(order_id=order_id, amo=amo)

    def get_order_book(self) -> Dict[str, Any]:
        """Fetch open / pending / historical order state."""
        self.ensure_logged_in()
        return self.client.order_report()

    def get_trade_book(self) -> Dict[str, Any]:
        """Fetch executed trades."""
        self.ensure_logged_in()
        return self.client.trade_report()

    def get_positions(self, kind: str = "net") -> Dict[str, Any]:
        """Fetch day + net positions."""
        self.ensure_logged_in()
        try:
            result = self.client.positions()
            
            # Normalize broker response to frontend format
            if isinstance(result, dict):
                # Check if broker returned "No Data" error
                if result.get("stCode") == 5203 or result.get("errMsg") == "No Data":
                    return {"positions": []}
                
                raw_positions = _records(result)
            elif isinstance(result, list):
                raw_positions = result
            else:
                return {"positions": []}
            
            # Neo's current positions endpoint can return fill-shaped rows
            # (sym/trdSym, trnsTp, fldQty) rather than a pre-aggregated net
            # position. Combine those fills so executed quantities are visible
            # in the terminal's Positions panel.
            positions_by_symbol: dict[str, dict[str, Any]] = {}
            for p in raw_positions:
                symbol = str(_first_value(
                    p,
                    "displaySymbol",
                    "tradingSymbol",
                    "pTrdSymbol",
                    "trdSym",
                    "sym",
                    "symbol",
                    "scripName",
                    "commonScripCode",
                    default="",
                )).strip()
                if not symbol:
                    continue

                component_fields = ("cfBuyQty", "flBuyQty", "cfSellQty", "flSellQty")
                if any(field in p and p[field] not in (None, "") for field in component_fields):
                    qty = (
                        _integer(p.get("cfBuyQty"))
                        + _integer(p.get("flBuyQty"))
                        - _integer(p.get("cfSellQty"))
                        - _integer(p.get("flSellQty"))
                    )
                    order_qty = qty
                    lot_size = _number(_first_value(p, "lotSz", "lotSize", default=1), 1)
                    exchange_segment = str(_first_value(p, "exSeg", "exchangeSegment", default="")).lower()
                    if exchange_segment.endswith("_fo") and lot_size > 1:
                        qty = int(qty / lot_size)
                else:
                    net_qty = _first_value(p, "netQty", "netQuantity", "netPosition", "net_position", default=None)
                    if net_qty is not None:
                        qty = _integer(net_qty)
                    else:
                        raw_qty = _first_value(p, "qty", "quantity", "buyQty", "sellQty", default=None)
                        qty = _integer(raw_qty) if raw_qty is not None else 0
                        fill_qty = _first_value(p, "fldQty", "filledQty", "filledQuantity", "tradeQty", default=None)
                        # Kotak fill rows often contain qty=0 alongside fldQty.
                        if fill_qty is not None and (raw_qty is None or qty == 0):
                            fill_qty = _integer(fill_qty)
                            side = str(_first_value(p, "trnsTp", "transactionType", "side", default="B")).upper()
                            qty = -fill_qty if side in {"S", "SELL"} else fill_qty
                    order_qty = qty

                if qty == 0:
                    continue

                position = positions_by_symbol.setdefault(
                    symbol,
                    {
                        "symbol": symbol,
                        "exchange_segment": str(_first_value(p, "exSeg", "exchange_segment", "exchangeSegment", default="nse_cm")).lower(),
                        "instrument_token": str(_first_value(p, "tok", "pSymbol", "instrument_token", "instrumentToken", default="")).split("|")[-1],
                        "product": str(_first_value(p, "product", "prod", "pCode", "productCode", "prd", default="")).strip().upper(),
                        "qty": 0,
                        "order_quantity": 0,
                        "buy_qty": 0,
                        "buy_value": 0.0,
                        "sell_qty": 0,
                        "sell_value": 0.0,
                        "current_price": 0.0,
                        "pnl": 0.0,
                        "pnl_pct": 0.0,
                    },
                )
                if not position["instrument_token"]:
                    position["instrument_token"] = str(_first_value(
                        p,
                        "tok",
                        "pSymbol",
                        "instrument_token",
                        "instrumentToken",
                        default="",
                    )).split("|")[-1]
                if not position["product"]:
                    position["product"] = str(_first_value(
                        p, "product", "prod", "pCode", "productCode", "prd", default=""
                    )).strip().upper()
                avg_price = _number(_first_value(
                    p,
                    "avgPrc",
                    "avgPrice",
                    "averagePrice",
                    "buyAvgPrice",
                    "sellAvgPrice",
                ))
                if qty > 0:
                    position["buy_qty"] += qty
                    position["buy_value"] += qty * avg_price
                else:
                    position["sell_qty"] += abs(qty)
                    position["sell_value"] += abs(qty) * avg_price
                position["qty"] += qty
                position["order_quantity"] += order_qty
                position["current_price"] = position["current_price"] or _number(
                    _first_value(p, "lastRate", "ltp", "lastPrice", "closingPrice")
                )
                position["pnl"] += _number(
                    _first_value(p, "mtom", "pnl", "unrealisedGainLoss", "unrealizedGainLoss")
                )
                position["pnl_pct"] = _number(
                    _first_value(p, "mtomPct", "pnlPct", "pnlPercentage"),
                    position["pnl_pct"],
                )

            missing_by_segment: dict[str, list[dict[str, Any]]] = {}
            for position in positions_by_symbol.values():
                if position["qty"] and not position["instrument_token"]:
                    segment = position["exchange_segment"]
                    if segment in {"nse_cm", "bse_cm", "nse_fo", "bse_fo"}:
                        missing_by_segment.setdefault(segment, []).append(position)

            for segment, missing_positions in missing_by_segment.items():
                try:
                    master_rows = self._load_scrip_master(segment)
                except KotakClientError as exc:
                    logger.warning("Could not resolve position tokens for %s: %s", segment, str(exc)[:180])
                    continue
                for position in missing_positions:
                    symbol_key = position["symbol"].upper()
                    match = next((row for row in master_rows if str(_first_value(
                        row, "pTrdSymbol", "trading_symbol", "trdSym", "symbol", default=""
                    )).upper() == symbol_key), None)
                    if not match and symbol_key.endswith("-EQ"):
                        base_symbol = symbol_key[:-3]
                        match = next((row for row in master_rows if
                            str(_first_value(row, "pSymbolName", "symbol_name", "symbol", default="")).upper() == base_symbol
                            and str(_first_value(row, "pGroup", "series", "pSeries", default="EQ")).upper() in {"EQ", "BL"}
                        ), None)
                    if match:
                        position["instrument_token"] = str(_first_value(
                            match, "pSymbol", "instrument_token", "token", default=""
                        )).split("|")[-1]

            positions = []
            for position in positions_by_symbol.values():
                if position["qty"] == 0:
                    continue
                if position["qty"] > 0 and position["buy_qty"]:
                    position["avg_price"] = position["buy_value"] / position["buy_qty"]
                elif position["qty"] < 0 and position["sell_qty"]:
                    position["avg_price"] = position["sell_value"] / position["sell_qty"]
                else:
                    position["avg_price"] = 0.0
                positions.append({
                    "symbol": position["symbol"],
                    "qty": position["qty"],
                    "order_quantity": position["order_quantity"],
                    "avg_price": position["avg_price"],
                    "current_price": position["current_price"],
                    "pnl": position["pnl"],
                    "pnl_pct": position["pnl_pct"],
                    "exchange_segment": position["exchange_segment"],
                    "instrument_token": position["instrument_token"],
                    "product": position["product"],
                })

            return {"positions": positions}
        except Exception as exc:
            raise KotakClientError(f"Positions fetch failed: {exc}") from exc

    def get_holdings(self) -> Dict[str, Any]:
        """Fetch portfolio holdings."""
        self.ensure_logged_in()
        try:
            result = self.client.holdings()
            
            # Normalize broker response to frontend format
            if isinstance(result, dict):
                raw_holdings = _records(result)
            elif isinstance(result, list):
                raw_holdings = result
            else:
                return {"holdings": []}
            
            holdings = []
            for h in raw_holdings:
                holding = {
                    "symbol": h.get("displaySymbol") or h.get("symbol") or h.get("commonScripCode", ""),
                    "qty": int(h.get("quantity", 0)),
                    "avg_price": float(h.get("averagePrice", 0)),
                    "current_price": float(h.get("closingPrice", 0)),
                    "value": float(h.get("mktValue", 0)),
                }
                holdings.append(holding)
            
            return {"holdings": holdings}
        except Exception as exc:
            raise KotakClientError(f"Holdings fetch failed: {exc}") from exc

    def get_margin_and_funds(self) -> Dict[str, Any]:
        """Return available funds and margin values from Kotak's RMS limits."""
        self.ensure_logged_in()
        try:
            result = self.client.limits(segment="ALL", exchange="ALL", product="ALL")
            if not isinstance(result, (dict, list)):
                raise KotakClientError("Kotak returned an invalid limits response.")

            records = _records(result)
            if not records:
                raise KotakClientError("Kotak returned no account limits.")
            limit_record = records[0]

            for response_record in (result, limit_record):
                if not isinstance(response_record, dict):
                    continue
                error = _first_value(
                    response_record,
                    "Error Message",
                    "Error",
                    "error",
                    "emsg",
                    "errMsg",
                    default=None,
                )
                if error:
                    raise KotakClientError(f"Kotak limits request failed: {error}")

                status = str(
                    _first_value(response_record, "stat", "status", default="")
                ).strip().lower()
                if status and status not in {"ok", "success", "200"}:
                    raise KotakClientError(f"Kotak limits request returned status: {status}")

            # Kotak's v2 Limits response reports the remaining margin as Net.
            available_raw = _first_value(
                limit_record,
                "Net",
                "net",
                "AvailableMargin",
                "availableMargin",
                "available",
                "availableCash",
                "AvailableCash",
                default=None,
            )
            available = _optional_number(available_raw)
            if available is None:
                raise KotakClientError("Kotak limits response did not include a valid available-margin value.")

            margin_used_raw = _first_value(
                limit_record,
                "MarginUsed",
                "MarginUsedPrsnt",
                "marginUsed",
                "AmountUtilizedPrsnt",
                "AmtUntilizedPrsnt",
                "utilised",
                "Utilised",
                "utilized",
                "Utilized",
                default=None,
            )
            margin_used = _optional_number(margin_used_raw)
            if margin_used is None:
                raise KotakClientError("Kotak limits response did not include a valid MarginUsed value.")

            gross_raw = _first_value(
                limit_record,
                "GrossMarginAvailable",
                "grossMarginAvailable",
                "gross",
                "Gross",
                "CollateralValue",
                "collateralValue",
                default=None,
            )
            gross = _optional_number(gross_raw)
            if gross is None:
                # If Kotak omits a gross field, restore the utilized amount to Net.
                gross = available + margin_used

            realized_pnl = _optional_number(
                _first_value(limit_record, "RealizedMtomPrsnt", "realizedMtom", default=None)
            ) or 0.0
            unrealized_pnl = _optional_number(
                _first_value(limit_record, "UnrealizedMtomPrsnt", "unrealizedMtom", default=None)
            ) or 0.0

            return {
                "available": available,
                "utilised": margin_used,
                "gross": gross,
                "pnl": realized_pnl + unrealized_pnl,
            }
        except KotakClientError:
            raise
        except Exception as exc:
            raise KotakClientError(f"Margin fetch failed: {exc}") from exc

    def search_instruments(self, query: str, exchange: str = "NSE") -> Dict[str, Any]:
        """Search instruments by symbol or fuzzy match with a practical fallback."""
        search_key = (query or "").strip()
        exchange_segment = {
            "NSE": "nse_cm",
            "BSE": "bse_cm",
            "NFO": "nse_fo",
        }.get(exchange.upper(), "nse_cm")

        try:
            self.ensure_logged_in()
            result = self.client.search_scrip(exchange_segment=exchange_segment, symbol=search_key)
            
            # Normalize broker response to frontend format
            results = []
            raw_results = []
            
            if isinstance(result, list):
                raw_results = result
            elif isinstance(result, dict):
                # Handle different possible response formats from broker
                if "results" in result and isinstance(result["results"], list):
                    raw_results = result["results"]
                elif "data" in result and isinstance(result["data"], list):
                    raw_results = result["data"]
                elif isinstance(result, dict) and "message" not in result and "error" not in result:
                    raw_results = [result]
            
            # Normalize each result using broker field names
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                    
                normalized_item = {
                    "symbol": item.get("pSymbolName") or item.get("symbol") or item.get("displaySymbol") or item.get("pTrdSymbol") or "",
                    "trading_symbol": item.get("pTrdSymbol") or item.get("trading_symbol") or item.get("symbol") or "",
                    "name": item.get("pSymbolName") or item.get("name") or item.get("pDesc") or "",
                    "exchange": "NSE",
                    "instrument_token": str(item.get("pSymbol") or item.get("instrument_token") or item.get("token") or ""),
                    "exchange_segment": item.get("pExchSeg") or exchange_segment,
                }
                
                if normalized_item["symbol"]:
                    results.append(normalized_item)
            
            if results:
                return {"results": results}
            
            # If broker returned nothing, try fallback
            fallback = self._fallback_search(search_key, exchange_segment)
            return {"results": fallback, "message": "No data found with the given search information.Please try with other combinations." if not fallback else ""}
            
        except Exception as exc:
            fallback = self._fallback_search(search_key, exchange_segment)
            return {"results": fallback, "message": f"Error during search: {exc}" if not fallback else ""}

    def _fallback_search(self, query: str, exchange_segment: str) -> List[Dict[str, Any]]:
        """Fallback symbol catalog to keep the terminal usable when broker search is empty."""
        aliases = {
            "nifty 50": "NIFTY 50",
            "nifty50": "NIFTY 50",
            "banknifty": "BANKNIFTY",
            "bank nifty": "BANKNIFTY",
            "reliance": "RELIANCE",
            "infy": "INFY",
            "tcs": "TCS",
            "icici": "ICICIBANK",
            "sbin": "SBIN",
            "hdfc": "HDFCBANK",
            "ltim": "LTIM",
            "axis": "AXISBANK",
        }

        normalized = query.lower().strip()
        symbol = aliases.get(normalized, query.upper().strip())

        catalog = [
            {"symbol": "NIFTY 50", "name": "Nifty 50 Index", "exchange": "NSE", "instrument_token": "", "exchange_segment": "nse_cm"},
            {"symbol": "BANKNIFTY", "name": "Bank Nifty Index", "exchange": "NSE", "instrument_token": "", "exchange_segment": "nse_cm"},
            {"symbol": "RELIANCE", "trading_symbol": "RELIANCE-EQ", "name": "Reliance Industries", "exchange": "NSE", "instrument_token": "2885", "exchange_segment": "nse_cm"},
            {"symbol": "TCS", "trading_symbol": "TCS-EQ", "name": "Tata Consultancy Services", "exchange": "NSE", "instrument_token": "11536", "exchange_segment": "nse_cm"},
            {"symbol": "INFY", "trading_symbol": "INFY-EQ", "name": "Infosys", "exchange": "NSE", "instrument_token": "1594", "exchange_segment": "nse_cm"},
            {"symbol": "HDFCBANK", "trading_symbol": "HDFCBANK-EQ", "name": "HDFC Bank", "exchange": "NSE", "instrument_token": "1333", "exchange_segment": "nse_cm"},
            {"symbol": "ICICIBANK", "trading_symbol": "ICICIBANK-EQ", "name": "ICICI Bank", "exchange": "NSE", "instrument_token": "4963", "exchange_segment": "nse_cm"},
            {"symbol": "SBIN", "trading_symbol": "SBIN-EQ", "name": "State Bank of India", "exchange": "NSE", "instrument_token": "3045", "exchange_segment": "nse_cm"},
        ]

        if not normalized:
            return catalog[:5]

        matches = [entry for entry in catalog if normalized in entry["symbol"].lower() or normalized in entry["name"].lower()]
        if not matches and symbol:
            matches = [entry for entry in catalog if entry["symbol"].startswith(symbol[:4]) or symbol in entry["symbol"]]
        return matches

    def get_option_chain(self, symbol: str, expiry: str | None = None) -> Dict[str, Any]:
        """Fetch option chain information for index / F&O instruments."""
        self.ensure_logged_in()
        try:
            requested_symbol = " ".join(symbol.upper().strip().split())
            index_specs = {
                "NIFTY": ("NIFTY", "nse_fo", "nse_cm", "Nifty 50"),
                "NIFTY 50": ("NIFTY", "nse_fo", "nse_cm", "Nifty 50"),
                "NIFTY50": ("NIFTY", "nse_fo", "nse_cm", "Nifty 50"),
                "BANKNIFTY": ("BANKNIFTY", "nse_fo", "nse_cm", "Nifty Bank"),
                "NIFTY BANK": ("BANKNIFTY", "nse_fo", "nse_cm", "Nifty Bank"),
                "SENSEX": ("SENSEX", "bse_fo", "bse_cm", "SENSEX"),
            }
            spec = index_specs.get(requested_symbol)
            if not spec:
                raise KotakClientError(f"Option chains are supported for NIFTY, BANKNIFTY, and SENSEX; got {symbol}.")
            search_symbol, option_segment, underlying_segment, underlying_token = spec
            resolved_expiry = _resolve_expiry(expiry)
            try:
                result = self.client.search_scrip(exchange_segment=option_segment, symbol=search_symbol, expiry=resolved_expiry, option_type="", strike_price="")
            except Exception:
                time.sleep(0.5)
                result = self.client.search_scrip(exchange_segment=option_segment, symbol=search_symbol, expiry=resolved_expiry, option_type="", strike_price="")

            if isinstance(result, dict):
                if result.get("error") or result.get("Error") or result.get("message"):
                    errors = result.get("error")
                    error_message = ""
                    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                        error_message = str(errors[0].get("message") or errors[0].get("error") or "")
                    return {
                        "chains": [],
                        "symbol": symbol,
                        "expiry": expiry or "current",
                        "message": result.get("message") or error_message or "No option contracts found.",
                    }
                contracts = result.get("results") or result.get("data") or []
            elif isinstance(result, list):
                contracts = result
            else:
                contracts = []

            exact_contracts = [
                contract for contract in contracts
                if isinstance(contract, dict)
                and str(contract.get("pSymbolName") or contract.get("symbol") or "").strip().upper() == search_symbol.upper()
            ]
            if exact_contracts:
                contracts = exact_contracts

            underlying_ltp = 0.0
            underlying_quote_error = ""
            try:
                underlying_quotes = self.get_quotes(
                    [underlying_token], exchange_segment=underlying_segment, quote_type="all"
                )["quotes"]
                if underlying_quotes:
                    underlying_ltp = _number(underlying_quotes[0].get("ltp"))
            except Exception as exc:
                underlying_quote_error = f"Underlying index quote unavailable: {exc}"

            grouped: dict[float, dict[str, Any]] = {}
            quote_tokens: list[dict[str, str]] = []
            contract_by_symbol: dict[str, tuple[dict[str, Any], str]] = {}
            dated_contracts = []
            today = datetime.now()
            for contract in contracts:
                contract_expiry = _expiry_date(contract.get("pExpiryDate") or contract.get("expiry"))
                if contract_expiry and contract_expiry >= today.replace(hour=0, minute=0, second=0, microsecond=0):
                    dated_contracts.append((contract_expiry, contract))

            if not resolved_expiry and dated_contracts:
                today_date = today.replace(hour=0, minute=0, second=0, microsecond=0)
                future_expiries = [item[0] for item in dated_contracts if item[0] >= today_date]
                nearest_expiry = min(future_expiries or [item[0] for item in dated_contracts])
                contracts = [item[1] for item in dated_contracts if item[0] == nearest_expiry]
            elif resolved_expiry:
                requested_expiry = _expiry_date(resolved_expiry)
                if requested_expiry:
                    matching_contracts = [
                        contract for contract in contracts
                        if _expiry_date(contract.get("pExpiryDate") or contract.get("expiry"))
                        and _expiry_date(contract.get("pExpiryDate") or contract.get("expiry")).date() == requested_expiry.date()
                    ]
                    if matching_contracts:
                        contracts = matching_contracts
            for contract in contracts:
                if not isinstance(contract, dict):
                    continue
                option_type = str(contract.get("pOptionType") or contract.get("option_type") or "").upper()
                if option_type not in {"CE", "PE"}:
                    continue
                raw_strike = contract.get("dStrikePrice;") or contract.get("strike_price") or contract.get("strikePrice")
                if raw_strike in (None, ""):
                    continue
                strike = float(raw_strike)
                if "dStrikePrice;" in contract:
                    strike /= 100
                row = grouped.setdefault(strike, {"strike_price": strike})
                prefix = "call" if option_type == "CE" else "put"
                row[f"{prefix}_symbol"] = contract.get("pTrdSymbol") or contract.get("trading_symbol") or ""
                row[f"{prefix}_expiry"] = contract.get("pExpiryDate") or contract.get("expiry") or ""
                row[f"{prefix}_lot_size"] = contract.get("lLotSize") or contract.get("lot_size") or 1
                instrument_token = contract.get("pSymbol") or contract.get("instrument_token") or contract.get("token")
                if instrument_token:
                    row[f"{prefix}_token"] = str(instrument_token).split("|")[-1]

            all_strikes = sorted(grouped)
            if all_strikes:
                atm_index = min(
                    range(len(all_strikes)),
                    key=lambda index: abs(all_strikes[index] - underlying_ltp),
                ) if underlying_ltp > 0 else len(all_strikes) // 2
                start = max(0, min(atm_index - 5, len(all_strikes) - 11))
                selected_strikes = all_strikes[start:start + 11]
            else:
                atm_index = 0
                selected_strikes = []
            selected_grouped = {strike: grouped[strike] for strike in selected_strikes}
            quote_tokens = []
            contract_by_symbol = {}
            for row in selected_grouped.values():
                for prefix in ("call", "put"):
                    token = row.get(f"{prefix}_token", "")
                    if token:
                        quote_tokens.append({"exchange_segment": option_segment, "instrument_token": token})
                        contract_by_symbol[token] = (row, prefix)

            quote_error = ""
            if quote_tokens:
                try:
                    quote_result = self.client.quotes(instrument_tokens=quote_tokens, quote_type="all")
                    if isinstance(quote_result, dict) and (quote_result.get("error") or quote_result.get("Error")):
                        self._raise_for_broker_error(quote_result, "Option quote fetch failed")
                    if isinstance(quote_result, dict) and quote_result.get("fault"):
                        raise KotakClientError(f"Option quote fetch returned a broker fault: {quote_result['fault']}")
                    for quote in _quote_records(quote_result):
                        token = _quote_token(quote)
                        target = contract_by_symbol.get(token)
                        if not target:
                            continue
                        row, prefix = target
                        row[f"{prefix}_bid"] = _number(_quote_value(quote, "bid", "bestBid", "best_bid", "buyPrice", "buy_price", "bp1", "bp"), 0)
                        row[f"{prefix}_ask"] = _number(_quote_value(quote, "ask", "bestAsk", "best_ask", "sellPrice", "sell_price", "sp1", "sp"), 0)
                        row[f"{prefix}_ltp"] = _number(_quote_value(quote, "ltp", "lastPrice", "lastTradedPrice", "last_traded_price", "lp"), 0)
                        row[f"{prefix}_volume"] = _integer(_quote_value(quote, "volume", "tradedVolume", "volume_traded", "v"))
                        row[f"{prefix}_oi"] = _integer(_quote_value(quote, "openInterest", "open_interest", "oi", "oi_day"))
                except Exception as exc:
                    quote_error = f"Live option quotes unavailable: {exc}"
            chains = [selected_grouped[strike] for strike in selected_strikes]
            atm_strike = min(selected_strikes, key=lambda strike: abs(strike - underlying_ltp)) if selected_strikes and underlying_ltp > 0 else (selected_strikes[len(selected_strikes) // 2] if selected_strikes else 0)
            return {
                "chains": chains,
                "symbol": search_symbol,
                "expiry": resolved_expiry or "nearest",
                "exchange_segment": option_segment,
                "ltp": underlying_ltp,
                "atm_strike": atm_strike,
                "message": underlying_quote_error or quote_error or ("Live quote fields are unavailable." if not quote_tokens else ""),
            }
        except Exception as exc:
            raise KotakClientError(f"Option chain fetch failed: {exc}") from exc

    def modify_order(self, order_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Modify an existing order."""
        self.ensure_logged_in()
        try:
            return self.client.modify_order(order_id=order_id, **kwargs)
        except Exception as exc:
            raise KotakClientError(f"Modify order failed: {exc}") from exc

    def cancel_order(self, order_id: str, amo: str = "NO") -> Dict[str, Any]:
        """Cancel one order."""
        self.ensure_logged_in()
        try:
            return self.client.cancel_order(order_id=order_id, amo=amo, isVerify=True)
        except Exception as exc:
            raise KotakClientError(f"Cancel order failed: {exc}") from exc

    def get_order_book(self) -> Dict[str, Any]:
        """Fetch open / pending / historical order state."""
        self.ensure_logged_in()
        try:
            result = self.client.order_report()

            # Kotak's order report returns {stat, data: [...], stCode}. Treat
            # broker errors separately from a valid empty order list so the UI
            # cannot silently present a failed/malformed response as "No orders".
            if isinstance(result, dict):
                broker_code = str(result.get("stCode", "")).strip()
                broker_error = (
                    result.get("Error")
                    or result.get("Error Message")
                    or result.get("error")
                    or result.get("errMsg")
                    or result.get("emsg")
                )
                broker_status = str(result.get("stat", "")).strip().lower()
                if broker_code == "5203" or str(result.get("errMsg", "")).strip().lower() == "no data":
                    empty_signature = (
                        "no_data",
                        broker_code,
                        str(result.get("stat", "")),
                        str(result.get("errMsg", "")),
                        tuple(sorted(result.keys())),
                    )
                    if getattr(self, "_last_order_book_signature", None) != empty_signature:
                        logger.warning(
                            "Kotak order book returned no data: stCode=%r stat=%r errMsg=%r envelope_keys=%s",
                            broker_code,
                            result.get("stat"),
                            result.get("errMsg"),
                            empty_signature[4],
                        )
                        self._last_order_book_signature = empty_signature
                    return {
                        "orders": [],
                        "message": "Kotak returned No Data for the current order book. No orders were supplied by the broker.",
                    }

                if broker_error or broker_status in {"not_ok", "failed", "failure"}:
                    detail = str(broker_error or result.get("stat") or "unknown Kotak error")
                    raise KotakClientError(f"Kotak order report failed: {detail}")

                raw_orders = _records(result)
            elif isinstance(result, list):
                raw_orders = result
            else:
                raise KotakClientError(
                    f"Kotak returned an unsupported order report response ({type(result).__name__})."
                )

            # A successful envelope without order rows should not become a
            # fake, blank order card.
            raw_orders = [
                item for item in raw_orders
                if isinstance(item, dict)
                and _first_value(item, "orderId", "orderID", "order_id", "nOrdNo")
            ]
            response_signature = (
                str(result.get("stat", "")) if isinstance(result, dict) else "list",
                str(result.get("stCode", "")) if isinstance(result, dict) else "",
                tuple(sorted(result.keys())) if isinstance(result, dict) else (),
                len(raw_orders),
                tuple(sorted(raw_orders[0].keys())) if raw_orders else (),
            )
            if getattr(self, "_last_order_book_signature", None) != response_signature:
                logger.warning(
                    "Kotak order book response summary: stat=%r stCode=%r envelope_keys=%s rows=%d row_keys=%s",
                    response_signature[0],
                    response_signature[1],
                    response_signature[2],
                    response_signature[3],
                    response_signature[4],
                )
                self._last_order_book_signature = response_signature

            orders = []
            for o in raw_orders:
                validity = str(_first_value(o, "validity", "orderValidity", "ordValDt", "vldt", "vd", default="DAY") or "DAY").strip().upper()
                if validity in {"", "NA", "NONE"}:
                    validity = "DAY"
                order = {
                    "order_id": str(_first_value(o, "orderId", "orderID", "order_id", "nOrdNo") or ""),
                    "symbol": _first_value(o, "displaySymbol", "tradingSymbol", "pTrdSymbol", "trdSym", "symbol", "sym", "scripName", "scripCode"),
                    "side": str(_first_value(o, "transactionType", "transaction_type", "trnsTp", "side")).upper(),
                    "qty": _integer(_first_value(o, "quantity", "orderQuantity", "qty", "ordQty", "totalQuantity")),
                    "filled_qty": _optional_integer(_first_value(o, "fldQty", "filledQty", "filledQuantity", "filled_qty", default=None)),
                    "unfilled_qty": _optional_integer(_first_value(o, "unFldSz", "unfilledQty", "unfilledQuantity", "unfilled_qty", default=None)),
                    "price": _number(_first_value(o, "orderPrice", "price", "prc", "averagePrice", "avgPrice")),
                    "order_type": str(_first_value(o, "orderType", "order_type", "ordTyp", "prcTp", "pt", default="L")).upper(),
                    "validity": validity,
                    "amo": "YES" if str(_first_value(o, "amo", "isAmo", "ordGenTp", "am", default="NO")).strip().upper() in {"YES", "TRUE", "1", "AMO"} else "NO",
                    "status": str(_first_value(o, "ordSt", "orderStatus", "order_status", "status", "stat", default="UNKNOWN") or "UNKNOWN").upper(),
                    "timestamp": str(_first_value(o, "orderTime", "orderDateTime", "ordDtTm", "timestamp")),
                }
                if order["side"] == "B":
                    order["side"] = "BUY"
                elif order["side"] == "S":
                    order["side"] = "SELL"
                orders.append(order)
            
            return {"orders": orders}
        except Exception as exc:
            raise KotakClientError(f"Order book fetch failed: {exc}") from exc

    def get_trade_book(self) -> Dict[str, Any]:
        """Fetch executed trades."""
        self.ensure_logged_in()
        try:
            result = self.client.trade_report()
            
            # Normalize broker response to frontend format
            if isinstance(result, dict):
                # Check if broker returned "No Data" error
                if result.get("stCode") == 5203 or result.get("errMsg") == "No Data":
                    return {"trades": []}
                
                raw_trades = _records(result)
            elif isinstance(result, list):
                raw_trades = result
            else:
                return {"trades": []}
            
            trades = []
            for t in raw_trades:
                trade_time = _first_value(t, "tradeTime", "tradeDateTime", "timestamp", "exTm", default="")
                if not trade_time:
                    trade_date = _first_value(t, "flDt", default="")
                    fill_time = _first_value(t, "flTm", default="")
                    trade_time = f"{trade_date} {fill_time}".strip()
                trade = {
                    "trade_id": str(_first_value(t, "tradeId", "trade_id", "flId", "nOrdNo", default="")),
                    "symbol": _first_value(t, "displaySymbol", "tradingSymbol", "pTrdSymbol", "trdSym", "sym", "symbol", "scripCode", default=""),
                    "side": str(_first_value(t, "transactionType", "trnsTp", "side", default="")).upper(),
                    "qty": _integer(_first_value(t, "tradeQty", "trdQty", "fldQty", "quantity", "qty")),
                    "price": _number(_first_value(t, "tradePrice", "avgPrc", "averagePrice", "price", "prc")),
                    "timestamp": str(trade_time),
                }
                if trade["side"] == "B":
                    trade["side"] = "BUY"
                elif trade["side"] == "S":
                    trade["side"] = "SELL"
                trades.append(trade)
            
            return {"trades": trades}
        except Exception as exc:
            raise KotakClientError(f"Trade book fetch failed: {exc}") from exc
