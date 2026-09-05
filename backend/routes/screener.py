from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from screener.daily_scan import ScreenerDatabase, run_daily_scan

router = APIRouter(prefix="/screener", tags=["screener"])


@router.get("/scan")
def scan(mode: str = "synthetic", min_rr: float = 2.0) -> dict:
    if mode not in {"synthetic", "real"}:
        raise HTTPException(status_code=400, detail="mode must be synthetic or real")
    if min_rr <= 0:
        raise HTTPException(status_code=400, detail="min_rr must be greater than zero")

    result = run_daily_scan(mode=mode, min_rr=min_rr)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Screener failed"))

    return {
        "mode": mode,
        "scan_run_id": result["scan_run_id"],
        "candidate_count": result["candidate_count"],
        "candidates": [asdict(candidate) for candidate in result["candidates"]],
    }


@router.get("/latest")
def latest(mode: str = "real") -> dict:
    if mode not in {"synthetic", "real"}:
        raise HTTPException(status_code=400, detail="mode must be synthetic or real")
    return ScreenerDatabase().get_latest_scan(mode=mode)
