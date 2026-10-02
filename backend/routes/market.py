from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

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


@router.websocket("/option-chain/stream")
async def option_chain_stream(websocket: WebSocket, tokens: str = "") -> None:
    await websocket.accept()
    client = get_session_manager().get_client()
    if not client:
        await websocket.send_json({"type": "status", "state": "unauthorized", "message": "Please log in to Kotak Neo."})
        await websocket.close(code=4401)
        return

    parsed_tokens: list[dict[str, str]] = []
    for token_spec in tokens.split(","):
        if not token_spec:
            continue
        if "|" not in token_spec:
            await websocket.send_json({"type": "status", "state": "error", "message": "Invalid option token for live prices."})
            await websocket.close(code=1008)
            return
        exchange_segment, instrument_token = token_spec.split("|", 1)
        if exchange_segment not in {"nse_fo", "bse_fo"} or not instrument_token.isalnum():
            await websocket.send_json({"type": "status", "state": "error", "message": "Invalid option token for live prices."})
            await websocket.close(code=1008)
            return
        parsed_tokens.append({"exchange_segment": exchange_segment, "instrument_token": instrument_token})

    if not parsed_tokens or len(parsed_tokens) > 50:
        await websocket.send_json({"type": "status", "state": "error", "message": "The option chain must contain between 1 and 50 price tokens."})
        await websocket.close(code=1008)
        return

    try:
        async for event in client.option_chain_tick_stream(parsed_tokens):
            await websocket.send_json(event)
    except Exception as exc:
        detail = str(exc)[:240] if isinstance(exc, KotakClientError) else type(exc).__name__
        try:
            await websocket.send_json({"type": "status", "state": "error", "message": detail})
            await websocket.close(code=1011)
        except WebSocketDisconnect:
            pass
