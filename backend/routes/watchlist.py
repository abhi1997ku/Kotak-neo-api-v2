from fastapi import APIRouter

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("/")
def list_watchlist() -> dict:
    return {"watchlist": [], "status": "stub"}


@router.post("/add")
def add_symbol(symbol: str) -> dict:
    return {"status": "stub", "symbol": symbol, "action": "add"}


@router.delete("/remove")
def remove_symbol(symbol: str) -> dict:
    return {"status": "stub", "symbol": symbol, "action": "remove"}
