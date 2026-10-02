import asyncio
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager

router = APIRouter(prefix="/watchlist", tags=["watchlist"])
logger = logging.getLogger(__name__)


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return


async def _send_live_events(websocket: WebSocket, client) -> None:
    async for event in client.watchlist_tick_stream():
        await websocket.send_json(event)


@router.get("/")
def list_watchlist() -> dict:
    session_mgr = get_session_manager()
    client = session_mgr.get_client()
    if not client:
        raise HTTPException(status_code=401, detail="Kotak session is not active. Please login first.")
    try:
        return client.get_watchlist()
    except KotakClientError as exc:
        status_code = 401 if "session is not active" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.websocket("/stream")
async def stream_watchlist(websocket: WebSocket) -> None:
    await websocket.accept()
    client = get_session_manager().get_client()
    if not client:
        await websocket.send_json({"type": "status", "state": "unauthorized", "message": "Please log in to Kotak Neo."})
        await websocket.close(code=4401)
        return

    try:
        stream_task = asyncio.create_task(_send_live_events(websocket, client))
        disconnect_task = asyncio.create_task(_wait_for_disconnect(websocket))
        done, pending = await asyncio.wait(
            {stream_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            await task
    except WebSocketDisconnect:
        return
    except Exception as exc:
        detail = str(exc)[:240] if isinstance(exc, KotakClientError) else type(exc).__name__
        logger.warning("Kotak watchlist stream stopped: %s", detail)
        try:
            await websocket.send_json(
                {
                    "type": "status",
                    "state": "error",
                    "message": "Kotak's live market feed could not connect. Please check your session and try again.",
                }
            )
            await websocket.close(code=1011)
        except WebSocketDisconnect:
            pass


@router.post("/add")
def add_symbol(symbol: str) -> dict:
    return {"status": "stub", "symbol": symbol, "action": "add"}


@router.delete("/remove")
def remove_symbol(symbol: str) -> dict:
    return {"status": "stub", "symbol": symbol, "action": "remove"}
