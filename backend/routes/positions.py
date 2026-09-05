from fastapi import APIRouter, HTTPException

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("/")
def positions(kind: str = "net") -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        return client.get_positions(kind=kind)
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/holdings")
def holdings() -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        return client.get_holdings()
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/margin")
def margin() -> dict:
    try:
        session_mgr = get_session_manager()
        client = session_mgr.get_client()
        if not client:
            raise KotakClientError("Kotak session is not active. Please login first.")
        return client.get_margin_and_funds()
    except KotakClientError as e:
        raise HTTPException(status_code=401, detail=str(e))
