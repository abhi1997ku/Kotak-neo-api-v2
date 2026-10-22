"""Stock paper endpoints. No broker imports or authenticated calls."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from backend.stock_paper_service import service
from screener.swing_store import load_config, save_config, research_summary
from dataclasses import asdict

router = APIRouter(prefix="/screener", tags=["stock-paper"])

@router.get("/paper")
def portfolio():
    return service.status()

@router.post("/paper/scan", status_code=202)
def scan():
    return service.start()

@router.get("/paper/config")
def config():
    return asdict(load_config())

@router.post("/paper/config")
def update_config(values: dict):
    if not service.lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Wait for the current scan to finish")
    try:
        if any(o["status"] == "QUEUED" for o in service.store.read()["orders"]):
            raise HTTPException(status_code=409, detail="Queued signals must resolve before changing configuration")
        return save_config(values)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        service.lock.release()

@router.get("/paper/research")
def research():
    return research_summary()

@router.get("/paper/scans")
def scans():
    return service.store.history()

@router.get("/paper/scans/{scan_id}")
def scan_history(scan_id: int):
    try:
        return service.store.history(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/paper/export")
def export():
    payload = service.store.read()
    payload["scan_history"] = [dict(service.store.history(row["id"]), id=row["id"]) for row in service.store.history()]
    return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="stock-paper-history.json"'})

@router.get("/scan", status_code=410)
@router.get("/signal-performance", status_code=410)
@router.get("/latest", status_code=410)
@router.post("/import-feed", status_code=410)
def retired():
    return {"detail": "Legacy stock research is retired. Use /api/screener/paper for the broker-free paper portfolio."}
