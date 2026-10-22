"""Live-data endpoints for the paper-only BANKNIFTY experiment."""

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager
from backend.paper_agent import agent
from backend.paper_journal import PaperJournal

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])

@router.post("/start")
async def start() -> dict:
    if agent.strategy_retired:
        raise HTTPException(status_code=410, detail="This BANKNIFTY strategy has been retired. Historical results remain available.")
    client = get_session_manager().get_client()
    if not client: raise HTTPException(status_code=401, detail="Kotak session is not active. Please login first.")
    return agent.start(client)

@router.post("/stop")
def stop() -> dict: return agent.stop()

@router.get("/status")
def status() -> dict: return agent.status()

@router.get("/report")
def report() -> dict: return PaperJournal().report()

@router.get("/day-audit")
def day_audit() -> dict:
    return PaperJournal().day_audit(datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat())

@router.get("/report.csv")
def report_csv() -> Response:
    return Response(PaperJournal().csv(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=banknifty-paper-trades.csv"})


@router.get("/future-contract")
def future_contract() -> dict:
    """Resolve the contract separately so UI failures are actionable."""
    client = get_session_manager().get_client()
    if not client:
        raise HTTPException(status_code=401, detail="Kotak session is not active. Please login first.")
    try:
        contract = client._nearest_banknifty_future()
        return {
            "symbol": contract.get("pTrdSymbol") or contract.get("trading_symbol"),
            "instrument_token": str(contract.get("pSymbol") or contract.get("instrument_token") or ""),
            "lot_size": contract.get("lLotSize") or contract.get("lot_size"),
        }
    except KotakClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.websocket("/future-stream")
async def future_stream(websocket: WebSocket) -> None:
    """Expose the authenticated full-volume future stream; never places orders."""
    await websocket.accept()
    client = get_session_manager().get_client()
    if not client:
        await websocket.send_json({"type": "status", "state": "unauthorized", "message": "Please log in to Kotak Neo."})
        await websocket.close(code=4401)
        return
    try:
        async for event in client.banknifty_future_tick_stream():
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        detail = str(exc)[:240] if isinstance(exc, KotakClientError) else type(exc).__name__
        await websocket.send_json({"type": "status", "state": "error", "message": detail})
        await websocket.close(code=1011)
