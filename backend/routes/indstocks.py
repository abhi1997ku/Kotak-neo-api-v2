"""Paper-workspace-only read endpoints. Tokens are never supplied in URLs."""
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.indstocks_data import INDstocksError, client
from config import get_env

router = APIRouter(prefix="/paper-data/indstocks", tags=["INDstocks paper data"])


def index_tick(event: dict) -> dict | None:
    """Normalize only known spot-index LTP events; ignore acknowledgements."""
    import math
    raw = event.get("raw", {})
    token = str(raw.get("instrument", "")).removeprefix("NIDX:")
    if raw.get("mode") != "ltp" or token not in {"40000001", "40000003"}:
        return None
    value = raw.get("data", {}).get("ltp")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return {"state": "tick", "code": "NIDX_"+token, "price": value,
            "received_at": event["received_at"], "provider_timestamp": raw.get("timestamp"),
            "exchange_timestamp_verified": False}


@router.websocket("/indices-stream")
async def indices_stream(websocket: WebSocket) -> None:
    """Relay index LTP only; no order socket or browser-visible credential."""
    import asyncio
    await websocket.accept()
    source = None
    try:
        await websocket.send_json({"state": "connecting"})
        source = client.stream(["NIDX:40000001", "NIDX:40000003"], mode="ltp")
        while True:
            event = await asyncio.wait_for(anext(source), timeout=45)
            tick = index_tick(event)
            if tick:
                await websocket.send_json(tick)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.send_json({"state": "disconnected", "message": "No current ticks; market may be closed or feed unavailable"})
            await websocket.close()
        except RuntimeError:
            pass
    finally:
        if source is not None:
            await source.aclose()


@router.get("/option-paper")
def option_paper(limit: int = 20, offset: int = 0) -> dict:
    """Read durable local positions/history without contacting a broker."""
    import sqlite3
    from pathlib import Path
    from backend.option_paper_store import dashboard
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(422, "Invalid history page")
    try:
        return dashboard(Path(__file__).resolve().parents[1]/"data"/"ob_option_paper.sqlite", limit, offset)
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(503, "Paper journal temporarily unavailable") from None


@router.get("/order-block-research")
def order_block_research() -> dict:
    """Read a fixed local replay artifact; no market requests or order placement."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "data" / "order_block_research.json"
    if not path.exists():
        return {"state": "not_run", "orders_enabled": False, "fills_enabled": False, "summary": {}}
    try:
        result = json.loads(path.read_text())
        # Bound dashboard output while retaining full diagnostics on disk.
        result["reports"] = {symbol: {"as_of": report["as_of"], "outcomes": report.get("outcomes", []), "zones": report["zones"][-20:],
            "setups": report["setups"][-20:], "latest": report["rankings"][-1] if report["rankings"] else None}
            for symbol, report in result["reports"].items()}
        return {**result, "state": "historical_replay"}
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "Order-block replay report unavailable") from None


@router.get("/indices")
def indices() -> dict:
    """Verified INDstocks spot-index codes; no futures substituted for spot."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    regular = now.weekday() < 5 and "09:15" <= now.strftime("%H:%M") < "15:30"
    result = invoke(client.quotes, ["NIDX_40000001", "NIDX_40000003"])
    return {**result, "outside_regular_session": not regular}


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    """Relay BANKNIFTY market data only; credentials stay on the server."""
    import asyncio
    import pandas as pd
    from backend.indstocks_monitor import resolve_future
    await websocket.accept()
    source = None
    try:
        now = pd.Timestamp.now(tz="Asia/Kolkata")
        if now.weekday() >= 5 or not "09:15" <= now.strftime("%H:%M") < "15:30":
            await websocket.send_json({"state": "Outside regular session"})
            await websocket.close(code=1000)
            return
        future = resolve_future(now)
        await websocket.send_json({"state": "connecting", "contract": future["TRADING_SYMBOL"]})
        source = client.stream(["NFO:" + future["SECURITY_ID"]])
        while True:
            event = await asyncio.wait_for(anext(source), timeout=20)
            await websocket.send_json({"state": "receiving", **event})
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.send_json({"state": "disconnected", "message": "No current stream data; reconnecting required"})
            await websocket.close()
        except RuntimeError:
            pass
    finally:
        if source is not None:
            await source.aclose()


@router.get("/monitor")
def monitor() -> dict:
    """Read saved observations without contacting either broker."""
    import json
    import sqlite3
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo
    root = Path(__file__).resolve().parents[1] / "data"
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    state = {}
    path = root / "indstocks_ob_status.json"
    if path.exists():
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            state = {"state": "unreadable"}
    try:
        age = max(0, (now - datetime.fromisoformat(state["checked_at"])).total_seconds())
    except (KeyError, ValueError, TypeError):
        age = None
    rows = []
    database = root / "indstocks_ob_observations.sqlite"
    if database.exists():
        try:
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                entries = db.execute("SELECT payload,accepted FROM observations WHERE substr(observed_at,1,10)=? ORDER BY timestamp DESC", (str(now.date()),)).fetchall()
            rows = [{**json.loads(payload), "accepted": bool(accepted)} for payload, accepted in entries]
        except (sqlite3.Error, ValueError):
            raise HTTPException(503, "Saved observations are temporarily unavailable") from None
    return {"status": state, "stale": age is None or age > 150, "date": str(now.date()), "signals": rows,
            "accepted": sum(r["accepted"] for r in rows), "rejected": sum(not r["accepted"] for r in rows),
            "fills_enabled": False, "orders_enabled": False}


class HistoryRequest(BaseModel):
    identifiers: list[str] = Field(min_length=1, max_length=50)
    start_ms: int
    end_ms: int
    interval: str = "1minute"
    expired: bool = False


def invoke(function, *args, **kwargs):
    """Translate sanitized provider failures into explicit API errors."""
    try:
        return function(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except INDstocksError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/status")
def status() -> dict:
    return {"provider": "indstocks", "configured": bool(get_env("INDSTOCKS_ACCESS_TOKEN")),
            "mode": "data-only", "orders_enabled": False}


@router.post("/check")
def check() -> dict:
    return invoke(client.check_connection)


@router.get("/quotes")
def quotes(codes: str) -> dict:
    return invoke(client.quotes, codes.split(","))


@router.post("/history")
def history(body: HistoryRequest) -> dict:
    return invoke(client.history, **body.model_dump())

