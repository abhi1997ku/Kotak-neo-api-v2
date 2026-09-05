from fastapi import APIRouter, HTTPException

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/quotes")
def quotes(symbols: str, exchange_segment: str = "nse_cm") -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        sym_list = [item.strip() for item in symbols.split(",") if item.strip()]
        return client.get_quotes(sym_list, exchange_segment=exchange_segment)
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/search")
def search(query: str, exchange: str = "NSE") -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        return client.search_instruments(query, exchange=exchange)
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/option-chain")
def option_chain(symbol: str, expiry: str | None = None) -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        return client.get_option_chain(symbol, expiry=expiry)
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Option-chain broker request failed: {e}")
