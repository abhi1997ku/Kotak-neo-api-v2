from fastapi import APIRouter, HTTPException
from backend.intraday_stock_store import dashboard, evaluate

router = APIRouter(prefix="/paper/intraday-stocks", tags=["Intraday stock paper research"])


@router.get("")
def read():
    return dashboard()


@router.post("/scan")
def scan():
    try:
        return evaluate()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(422, "Cached data invalid, incomplete or stale; scan blocked") from None
