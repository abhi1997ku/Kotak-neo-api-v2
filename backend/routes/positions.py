import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager

router = APIRouter(prefix="/positions", tags=["positions"])


@router.websocket("/stream")
async def position_stream(websocket: WebSocket, tokens: str = "") -> None:
    await websocket.accept()
    client = get_session_manager().get_client()
    if not client:
        await websocket.send_json({"type": "status", "state": "unauthorized", "message": "Please log in to Kotak Neo."})
        await websocket.close(code=4401)
        return

    try:
        parsed = json.loads(tokens)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if not isinstance(parsed, list) or not parsed or len(parsed) > 50:
        await websocket.send_json({"type": "status", "state": "error", "message": "Position live prices need between 1 and 50 instrument tokens."})
        await websocket.close(code=1008)
        return

    parsed_tokens: list[dict[str, str]] = []
    allowed_segments = {"nse_cm", "bse_cm", "nse_fo", "bse_fo"}
    for token in parsed:
        if not isinstance(token, dict):
            continue
        exchange_segment = str(token.get("exchange_segment", "")).lower()
        instrument_token = str(token.get("instrument_token", ""))
        symbol = str(token.get("symbol", "")).strip()
        if exchange_segment not in allowed_segments or not instrument_token.isalnum() or not symbol:
            continue
        parsed_tokens.append({
            "exchange_segment": exchange_segment,
            "instrument_token": instrument_token,
            "symbol": symbol,
        })

    if not parsed_tokens:
        await websocket.send_json({"type": "status", "state": "error", "message": "No valid position price tokens were provided."})
        await websocket.close(code=1008)
        return

    try:
        async for event in client.position_tick_stream(parsed_tokens):
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        detail = str(exc)[:240] if isinstance(exc, KotakClientError) else type(exc).__name__
        try:
            await websocket.send_json({"type": "status", "state": "error", "message": detail})
            await websocket.close(code=1011)
        except WebSocketDisconnect:
            pass


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
    except KotakClientError as exc:
        message = str(exc)
        lower = message.lower()
        status_code = 401 if any(
            marker in lower
            for marker in ("session is not active", "please login first", "fresh login")
        ) else 502
        raise HTTPException(status_code=status_code, detail=message) from exc
