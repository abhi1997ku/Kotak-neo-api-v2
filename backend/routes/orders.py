from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager

router = APIRouter(prefix="/orders", tags=["orders"])

AMO_TRADE_API_UNAVAILABLE = (
    "Kotak has temporarily disabled AMO placement through Trade APIs. "
    "Place AMO orders through the Kotak Neo app or web platform."
)


class OrderRequest(BaseModel):
    exchange_segment: str
    product: str
    order_type: str
    transaction_type: str
    quantity: int
    price: float | None = None
    amo: str = "NO"
    trading_symbol: str | None = None
    validity: str = "DAY"
    trigger_price: float | None = None
    tag: str | None = None
    scrip_token: str | None = None


class ModifyOrderRequest(BaseModel):
    order_id: str
    order_type: str
    quantity: int = Field(gt=0)
    price: float = Field(ge=0)
    validity: str = "DAY"
    amo: str = "NO"


class CancelOrderRequest(BaseModel):
    order_id: str
    amo: str = "NO"


def broker_action_error(result: object) -> str | None:
    if not isinstance(result, dict):
        return None

    error = (
        result.get("Error")
        or result.get("Error Message")
        or result.get("error")
        or result.get("errMsg")
        or result.get("emsg")
    )
    if isinstance(error, list) and error:
        first_error = error[0]
        if isinstance(first_error, dict):
            error = first_error.get("message") or first_error.get("error") or first_error
    elif isinstance(error, dict):
        error = error.get("message") or error.get("error") or error.get("reason") or error

    if error:
        detail = str(error)
        reason = result.get("Reason") or result.get("rejRsn")
        if reason and str(reason) not in detail:
            detail = f"{detail}: {reason}"
        return detail

    if str(result.get("stat", "")).strip().lower() in {"not_ok", "failed", "failure"}:
        return str(result.get("errMsg") or result.get("message") or result.get("stat"))
    return None


def actionable_order(client: object, order_id: str) -> dict:
    book = client.get_order_book()
    order = next((item for item in book.get("orders", []) if item.get("order_id") == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order was not found in Kotak's current order book.")

    status = str(order.get("status") or "").strip().upper()
    if not status or status in {"UNKNOWN", "NONE"}:
        raise HTTPException(status_code=409, detail="Kotak did not return a status for this order, so it cannot be changed safely.")
    is_partially_filled = status.startswith(("PARTIAL", "PART ")) and any(token in status for token in ("FILL", "TRADE"))
    final_markers = ("CANCEL", "REJECT", "COMPLETE", "EXECUT", "EXPIRE", "CLOSED")
    trade_markers = ("TRADE", "FILL")
    is_final = any(marker in status for marker in final_markers) or (
        not is_partially_filled and any(marker in status for marker in trade_markers)
    )
    if is_final:
        raise HTTPException(status_code=409, detail=f"Order {order_id} is already {status.lower()} and cannot be changed.")
    return order


def order_error_status(message: str) -> int:
    lower = message.lower()
    local_session_errors = ("kotak session is not active", "please login first", "fresh login")
    return 401 if any(value in lower for value in local_session_errors) else 400


@router.post("/place")
def place_order(payload: OrderRequest) -> dict:
    if str(payload.amo or "NO").strip().upper() == "YES":
        raise HTTPException(status_code=503, detail=AMO_TRADE_API_UNAVAILABLE)
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        return client.place_order(
            exchange_segment=payload.exchange_segment,
            product=payload.product,
            order_type=payload.order_type,
            transaction_type=payload.transaction_type,
            quantity=payload.quantity,
            price=payload.price,
            amo=payload.amo,
            trading_symbol=payload.trading_symbol,
            validity=payload.validity,
            trigger_price=payload.trigger_price,
            tag=payload.tag,
            scrip_token=payload.scrip_token,
        )
    except KotakClientError as e:
        raise HTTPException(status_code=order_error_status(str(e)), detail=str(e))


@router.get("/book")
def order_book() -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        return client.get_order_book()
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/modify")
def modify_order(payload: ModifyOrderRequest) -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        current_order = actionable_order(client, payload.order_id)
        validity = str(current_order.get("validity") or payload.validity).strip().upper()
        if validity in {"", "NA", "NONE"}:
            validity = "DAY"
        result = client.modify_order(
            order_id=payload.order_id,
            price=str(payload.price),
            order_type=str(current_order.get("order_type") or payload.order_type).strip().upper(),
            quantity=str(payload.quantity),
            validity=validity,
            amo=str(current_order.get("amo") or payload.amo).strip().upper(),
        )
        error = broker_action_error(result)
        if error:
            raise KotakClientError(f"Modify order failed: {error}")
        return {"order_id": payload.order_id, "result": result}
    except KotakClientError as e:
        raise HTTPException(status_code=order_error_status(str(e)), detail=str(e))


@router.post("/cancel")
def cancel_order(payload: CancelOrderRequest) -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        current_order = actionable_order(client, payload.order_id)
        result = client.cancel_order(
            order_id=payload.order_id,
            amo=str(current_order.get("amo") or payload.amo).strip().upper(),
        )
        error = broker_action_error(result)
        if error:
            raise KotakClientError(f"Cancel order failed: {error}")
        return {"order_id": payload.order_id, "result": result}
    except KotakClientError as e:
        raise HTTPException(status_code=order_error_status(str(e)), detail=str(e))


@router.get("/trade-book")
def trade_book() -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        return client.get_trade_book()
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))
