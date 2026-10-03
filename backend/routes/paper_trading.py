"""Live-data endpoints for the paper-only BANKNIFTY experiment."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])


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
