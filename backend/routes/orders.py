from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager

router = APIRouter(prefix="/orders", tags=["orders"])


class OrderRequest(BaseModel):
    exchange_segment: str
    product: str
    order_type: str
    transaction_type: str
    quantity: int
    price: float | None = None
    trading_symbol: str | None = None
    validity: str = "DAY"
    trigger_price: float | None = None
    tag: str | None = None


@router.post("/place")
def place_order(payload: OrderRequest) -> dict:
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
            trading_symbol=payload.trading_symbol,
            validity=payload.validity,
            trigger_price=payload.trigger_price,
            tag=payload.tag,
        )
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))


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
