from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Add parent directory to path so we can import neo_api_client
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neo_api_client import NeoAPI


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


def _integer(value: Any, default: int = 0) -> int:
    return int(_number(value, default))


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
    token = str(_first_value(record, "instrument_token", "instrumentToken", "token", "exchangeToken", "tk", "symbolToken"))
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
        for key in ("data", "result", "results", "positions", "limits"):
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
        if "detail" in response and response["detail"]:
            raise KotakClientError(f"{action}: {response['detail']}")

    def ensure_logged_in(self) -> None:
        """Use the existing session if valid, otherwise trigger a re-login prompt."""
        if getattr(self.client.configuration, "edit_token", None) and getattr(self.client.configuration, "edit_sid", None):
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
        if response.get("error"):
            self._raise_for_broker_error(response, "Invalid TOTP")
        self.session.view_token = response.get("data", {}).get("token")
        self.session.sid = response.get("data", {}).get("sid")
        return response

    def login_with_mpin(self, mpin: str | None = None) -> Dict[str, Any]:
        """Complete the second factor and create a valid trade session."""
        if not mpin:
            raise KotakClientError("MPIN is required.")
        response = self.client.totp_validate(mpin=mpin)
        if response.get("error"):
            self._raise_for_broker_error(response, "MPIN validation failed")
        self.session.trade_token = response.get("data", {}).get("token")
        self.session.edit_sid = response.get("data", {}).get("sid")
        self.session.base_url = response.get("data", {}).get("baseUrl")
        self.session.data_center = response.get("data", {}).get("dataCenter")
        return response

    def refresh_session_if_needed(self) -> None:
        """Check for auth expiry and prompt re-login if needed."""
        try:
            self.ensure_logged_in()
        except KotakClientError:
            raise

    def get_quotes(self, symbols: List[str], exchange_segment: str = "nse_cm") -> Dict[str, Any]:
        """Fetch live quotes for a list of symbols when the broker_session is valid."""
        self.ensure_logged_in()
        try:
            payload = [{'instrument_token': token, 'exchange_segment': exchange_segment} for token in symbols]
            result = self.client.quotes(instrument_tokens=payload, quote_type='ltp')
            if isinstance(result, dict):
                return result
            return {'quotes': result or []}
        except Exception as exc:
            raise KotakClientError(f"Quote fetch failed: {exc}") from exc

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
        trading_symbol: str | None = None,
        validity: str = "DAY",
        trigger_price: float | None = None,
        tag: str | None = None,
    ) -> Dict[str, Any]:
        """Place an order through the SDK wrapper."""
        self.ensure_logged_in()
        return self.client.place_order(
            exchange_segment=exchange_segment,
            product=product,
            price=str(price if price is not None else 0),
            order_type=order_type,
            quantity=str(quantity),
            validity=validity,
            trading_symbol=trading_symbol or "",
            transaction_type=transaction_type,
            amo="NO",
            disclosed_quantity="0",
            market_protection="0",
            pf="N",
            trigger_price=str(trigger_price if trigger_price is not None else 0),
            tag=tag,
        )

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
            
            positions = []
            for p in raw_positions:
                position = {
                    "symbol": _first_value(p, "displaySymbol", "tradingSymbol", "pTrdSymbol", "symbol", "scripName", "commonScripCode"),
                    "qty": _integer(_first_value(p, "netQty", "netQuantity", "quantity", "buyQty", "sellQty")),
                    "avg_price": _number(_first_value(p, "avgPrice", "averagePrice", "buyAvgPrice", "sellAvgPrice")),
                    "current_price": _number(_first_value(p, "lastRate", "ltp", "lastPrice", "closingPrice")),
                    "pnl": _number(_first_value(p, "mtom", "pnl", "unrealisedGainLoss", "unrealizedGainLoss")),
                    "pnl_pct": _number(_first_value(p, "mtomPct", "pnlPct", "pnlPercentage")),
                }
                positions.append(position)
            
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
                raw_holdings = result.get("data", []) if "data" in result else (result if isinstance(result, list) else [])
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
        """Fetch funds, margin available, and utilization metrics."""
        self.ensure_logged_in()
        try:
            result = self.client.limits(segment="ALL", exchange="ALL", product="ALL")
            
            # Normalize broker response to frontend format
            if isinstance(result, dict):
                limit_record = _records(result)[0] if _records(result) else result
                net = _number(_first_value(limit_record, "Net", "net", "availableCash", "AvailableCash", "availableMargin", "AvailableMargin"))
                collateral = _number(_first_value(limit_record, "Collateral", "collateral", "collateralAmount"))
                margin_used = _number(_first_value(limit_record, "MarginUsed", "marginUsed", "utilised", "Utilised", "utilized", "Utilized"))
                collateral_value = _number(_first_value(limit_record, "CollateralValue", "collateralValue"))
                
                # Calculate available = Net + unused collateral
                available = net + (collateral_value - collateral) if collateral_value > collateral else net
                
                # gross = total available margin (collateral + cash)
                gross = net + collateral
                
                # pnl = 0 for now (can be calculated from holdings separately)
                pnl = 0.0
                
                return {
                    "available": available,
                    "utilised": margin_used,
                    "gross": gross,
                    "pnl": pnl,
                }
            
            return {
                "available": 0,
                "utilised": 0,
                "gross": 0,
                "pnl": 0,
            }
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
                    "symbol": item.get("pTrdSymbol") or item.get("symbol") or item.get("displaySymbol") or "",
                    "name": item.get("pSymbolName") or item.get("name") or item.get("pDesc") or "",
                    "exchange": "NSE",
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
            {"symbol": "NIFTY 50", "name": "Nifty 50 Index", "exchange": "NSE"},
            {"symbol": "BANKNIFTY", "name": "Bank Nifty Index", "exchange": "NSE"},
            {"symbol": "RELIANCE", "name": "Reliance Industries", "exchange": "NSE"},
            {"symbol": "TCS", "name": "Tata Consultancy Services", "exchange": "NSE"},
            {"symbol": "INFY", "name": "Infosys", "exchange": "NSE"},
            {"symbol": "HDFCBANK", "name": "HDFC Bank", "exchange": "NSE"},
            {"symbol": "ICICIBANK", "name": "ICICI Bank", "exchange": "NSE"},
            {"symbol": "SBIN", "name": "State Bank of India", "exchange": "NSE"},
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
            search_symbol = {"NIFTY 50": "NIFTY", "NIFTY50": "NIFTY"}.get(symbol.upper().strip(), symbol)
            resolved_expiry = _resolve_expiry(expiry)
            try:
                result = self.client.search_scrip(exchange_segment="nse_fo", symbol=search_symbol, expiry=resolved_expiry, option_type="", strike_price="")
            except Exception:
                time.sleep(0.5)
                result = self.client.search_scrip(exchange_segment="nse_fo", symbol=search_symbol, expiry=resolved_expiry, option_type="", strike_price="")

            if isinstance(result, dict):
                if result.get("error") or result.get("Error") or result.get("message"):
                    return {"chains": [], "symbol": symbol, "expiry": expiry or "current", "message": result.get("message") or "No option contracts found."}
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
                    row[f"{prefix}_token"] = str(instrument_token)

            all_strikes = sorted(grouped)
            if len(all_strikes) > 11:
                center = len(all_strikes) // 2
                selected_strikes = all_strikes[max(0, center - 5):center + 6]
            else:
                selected_strikes = all_strikes
            selected_grouped = {strike: grouped[strike] for strike in selected_strikes}
            quote_tokens = []
            contract_by_symbol = {}
            for row in selected_grouped.values():
                for prefix in ("call", "put"):
                    token = row.pop(f"{prefix}_token", "")
                    if token:
                        quote_tokens.append({"exchange_segment": "nse_fo", "instrument_token": token})
                        contract_by_symbol[token] = (row, prefix)

            quote_error = ""
            if quote_tokens:
                try:
                    quote_result = self.client.quotes(instrument_tokens=quote_tokens, quote_type="all")
                    print("QUOTE_RESPONSE_DEBUG", repr(quote_result)[:3000], flush=True)
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
            atm_strike = selected_strikes[len(selected_strikes) // 2] if selected_strikes else 0
            return {
                "chains": chains,
                "symbol": symbol,
                "expiry": resolved_expiry or "nearest",
                "ltp": 0,
                "atm_strike": atm_strike,
                "message": quote_error or ("Live quote fields are unavailable." if not quote_tokens else ""),
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
            return self.client.cancel_order(order_id=order_id, amo=amo)
        except Exception as exc:
            raise KotakClientError(f"Cancel order failed: {exc}") from exc

    def get_order_book(self) -> Dict[str, Any]:
        """Fetch open / pending / historical order state."""
        self.ensure_logged_in()
        try:
            result = self.client.order_report()
            
            # Normalize broker response to frontend format
            if isinstance(result, dict):
                # Check if broker returned "No Data" error
                if result.get("stCode") == 5203 or result.get("errMsg") == "No Data":
                    return {"orders": []}
                
                raw_orders = result.get("data", []) if "data" in result else (result if isinstance(result, list) else [])
            elif isinstance(result, list):
                raw_orders = result
            else:
                return {"orders": []}
            
            orders = []
            for o in raw_orders:
                order = {
                    "order_id": str(_first_value(o, "orderId", "orderID", "order_id")),
                    "symbol": _first_value(o, "displaySymbol", "tradingSymbol", "pTrdSymbol", "symbol", "scripName", "scripCode"),
                    "side": str(_first_value(o, "transactionType", "transaction_type", "side")).upper(),
                    "qty": _integer(_first_value(o, "quantity", "orderQuantity", "qty", "totalQuantity")),
                    "price": _number(_first_value(o, "orderPrice", "price", "averagePrice", "avgPrice")),
                    "status": str(_first_value(o, "orderStatus", "status", "order_status")).upper(),
                    "timestamp": str(_first_value(o, "orderTime", "orderDateTime", "timestamp")),
                }
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
                
                raw_trades = result.get("data", []) if "data" in result else (result if isinstance(result, list) else [])
            elif isinstance(result, list):
                raw_trades = result
            else:
                return {"trades": []}
            
            trades = []
            for t in raw_trades:
                trade = {
                    "trade_id": str(t.get("tradeId", t.get("trade_id", ""))),
                    "symbol": t.get("displaySymbol") or t.get("symbol") or t.get("scripCode", ""),
                    "side": t.get("transactionType", t.get("side", "")).upper(),
                    "qty": int(t.get("tradeQty", t.get("quantity", t.get("qty", 0)))),
                    "price": float(t.get("tradePrice", t.get("price", 0))),
                    "timestamp": str(t.get("tradeTime", t.get("timestamp", ""))),
                }
                trades.append(trade)
            
            return {"trades": trades}
        except Exception as exc:
            raise KotakClientError(f"Trade book fetch failed: {exc}") from exc
