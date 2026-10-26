"""Strict broker boundary for managed real orders. No automatic submission retries."""
import asyncio
import hashlib
import json
import math
from datetime import datetime, time
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import websockets

from backend.kotak_client import _first_value, _records, _nse_fo_row_expiry, _expiry_date


FINAL = {"complete", "completed", "filled", "traded", "cancelled", "canceled", "rejected", "expired"}


def status(value):
    return str(value or "").strip().lower()


def integer(value):
    number = float(value)
    if not math.isfinite(number) or number != int(number):
        raise ValueError("Broker returned an invalid quantity.")
    return int(number)


def validate_lot_quantity(quantity, lot):
    if lot <= 0 or quantity <= 0 or quantity % lot:
        raise ValueError(f"Quantity must be a positive multiple of the current lot size ({lot} contract units).")


def records(payload, allow_empty=True):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("Broker returned an unreadable report.")
    if str(payload.get("stCode")) == "5203" and allow_empty:
        return []
    if any(payload.get(k) for k in ("error", "Error", "Error Message", "errMsg", "emsg")) or status(payload.get("stat")) in {"not_ok", "failed", "failure"}:
        raise ValueError("Broker report failed; management is waiting for reconciliation.")
    if not any(isinstance(payload.get(k), list) for k in ("data", "orders", "positions", "result")):
        raise ValueError("Broker report has no identifiable rows.")
    return _records(payload)


def order_row(row):
    filled = _first_value(row, "fldQty", "filledQty", "filledQuantity", "filled_qty", default=None)
    quantity = _first_value(row, "qty", "quantity", "ordQty", "orderQuantity", default=None)
    return {
        "id": str(_first_value(row, "nOrdNo", "order_id", "orderId")),
        "tag": str(_first_value(row, "ig", "tag", "clientOrderId", "client_order_id")),
        "symbol": str(_first_value(row, "trdSym", "tradingSymbol", "pTrdSymbol", "symbol", "sym")),
        "token": str(_first_value(row, "tok", "tk", "token", "instrument_token", "pSymbol")),
        "segment": str(_first_value(row, "exSeg", "exchange_segment", "exchangeSegment")).lower(),
        "product": str(_first_value(row, "prod", "product", "pCode")).upper(),
        "side": str(_first_value(row, "trnsTp", "transactionType", "side")).upper(),
        "status": status(_first_value(row, "ordSt", "orderStatus", "status")),
        "filled": integer(filled) if filled is not None else None,
        "quantity": integer(quantity) if quantity is not None else None,
        "average": float(_first_value(row, "avgPrc", "averagePrice", default=0)),
    }


def matches(row, trade):
    return (row.get("token") == trade["instrument_token"] and row.get("segment") == trade["exchange_segment"]) or row.get("symbol") == trade["symbol"]


class ManagedBroker:
    def __init__(self, client):
        self.client = client
        ucc = str(client.config.get("ucc") or "")
        if not ucc:
            raise ValueError("Account identity is unavailable.")
        self.account = hashlib.sha256(ucc.encode()).hexdigest()

    def check_session(self):
        from backend.session_manager import get_session_manager
        if get_session_manager().get_client() is not self.client:
            raise ValueError("Trading session changed; reconcile before continuing.")

    def validate(self, request):
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        if now.weekday() >= 5 or not time(9, 15) <= now.time() < time(15, 15):
            raise ValueError("New managed entries are available on weekdays between 09:15 and 15:15 IST.")
        allowed = {"nse_fo": {"NIFTY", "BANKNIFTY"}, "bse_fo": {"SENSEX"}}
        if request.side != "BUY" or request.exchange_segment not in allowed:
            raise ValueError("Managed entries support buying NSE Nifty/Bank Nifty and BSE Sensex options only.")
        contracts = self.client._load_scrip_master(request.exchange_segment)
        contract = next((r for r in contracts if str(r.get("pSymbol")) == request.instrument_token and r.get("pTrdSymbol") == request.symbol), None)
        if not contract or str(contract.get("pSymbolName", "")).strip().upper() not in allowed[request.exchange_segment] or contract.get("pOptionType") not in {"CE", "PE"}:
            raise ValueError("Instrument token and symbol must match a supported index option in the selected exchange's current master.")
        if request.exchange_segment == "nse_fo":
            expiry = _nse_fo_row_expiry(contract)
        else:
            # BSE uses ordinary timestamps: do not apply the NSE epoch offset.
            expiries = [_expiry_date(contract.get(key)) for key in ("pExpiryDate", "lExpiryDate", "expiry", "expiry_date", "expiryDate")]
            expiry = max((value for value in expiries if value is not None), default=None)
        if not expiry or expiry.date() < now.date():
            raise ValueError("The option expiry could not be validated.")
        lot = integer(contract.get("lLotSize") or 0)
        validate_lot_quantity(request.quantity, lot)
        # Supported index-option premium prices use 0.05 steps.
        for price in (request.entry_limit, request.target_trigger, request.target_limit, request.stop_trigger, request.stop_limit):
            if price is not None and Decimal(str(price)) % Decimal("0.05"):
                raise ValueError("Option prices must be multiples of 0.05.")
        quotes = self.client.get_quotes([request.instrument_token], request.exchange_segment).get("quotes", [])
        ltp = float(quotes[0].get("ltp") or 0) if quotes else 0
        if not math.isfinite(ltp) or not request.stop_trigger < ltp < request.target_trigger:
            raise ValueError("Current premium must be available and between the stop and profit triggers before entry.")

    def orders(self):
        self.check_session()
        return [order_row(row) for row in records(self.client.client.order_report())]

    def position(self, trade):
        self.check_session()
        total = 0
        for row in records(self.client.client.positions()):
            key = {"symbol": str(_first_value(row, "trdSym", "tradingSymbol", "pTrdSymbol", "symbol", "sym")),
                   "token": str(_first_value(row, "tok", "pSymbol", "instrument_token")),
                   "segment": str(_first_value(row, "exSeg", "exchange_segment")).lower()}
            if not matches(key, trade):
                continue
            product = str(_first_value(row, "prod", "product", "pCode", "productCode", "prd")).upper()
            if product != trade["product"]:
                raise ValueError("Same instrument has another or unidentified product position; manage it manually.")
            components = ("cfBuyQty", "flBuyQty", "cfSellQty", "flSellQty")
            if all(row.get(k) not in (None, "") for k in components):
                total += integer(row[components[0]]) + integer(row[components[1]]) - integer(row[components[2]]) - integer(row[components[3]])
            elif _first_value(row, "netQty", "netQuantity", default=None) is not None:
                total += integer(_first_value(row, "netQty", "netQuantity"))
            elif row.get("fldQty") is not None and row.get("trnsTp") in {"B", "S"}:
                total += integer(row["fldQty"]) * (1 if row["trnsTp"] == "B" else -1)
            else:
                raise ValueError("Cannot establish position quantity in contract units.")
        return total

    def place(self, trade, leg, quantity, client_id):
        self.check_session()
        result = self.client.client.place_order(
            exchange_segment=trade["exchange_segment"], product=trade["product"],
            order_type=trade["entry_type"] if leg == "entry" else "L",
            transaction_type="B" if leg == "entry" else "S", quantity=str(quantity),
            price=str((trade.get("entry_limit") or 0) if leg == "entry" else trade["exit_limit"]),
            trading_symbol=trade["symbol"], scrip_token=trade["instrument_token"],
            validity="DAY", amo="NO", disclosed_quantity="0", market_protection="0", trigger_price="0", tag=client_id,
        )
        if not isinstance(result, dict) or status(result.get("stat")) != "ok" or not result.get("nOrdNo"):
            # Even an error may conceal an accepted request: never auto-resubmit.
            raise ValueError("No unambiguous acknowledgement. Check Kotak order book; no retry will be sent.")
        return str(result["nOrdNo"])

    def cancel(self, order_id):
        self.check_session()
        result = self.client.cancel_order(order_id)
        if not isinstance(result, dict) or status(result.get("stat")) != "ok":
            raise ValueError("Cancellation not confirmed. Reconcile before any exit submission.")

    async def order_events(self):
        base = urlsplit(self.client.session.base_url or "")
        if base.scheme != "https" or not base.netloc:
            raise ValueError("Order feed base URL is unavailable.")
        token, sid = self.client.session.trade_token, self.client.session.edit_sid
        async with websockets.connect(f"wss://{base.netloc}/realtime", open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(f"{{type:cn,Authorization:{token},Sid:{sid},src:WEB}}")
            ack = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if ack.get("ak") != "ok":
                raise ValueError("Order feed authentication failed.")
            yield {"type": "connected"}
            async for raw in ws:
                event = json.loads(raw)
                if isinstance(event, dict) and event.get("type") in {"order", "position"}:
                    yield event
