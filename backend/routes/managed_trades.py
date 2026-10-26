from typing import Literal

from fastapi import APIRouter, HTTPException

from backend.managed_trades import ManagedTradeRequest, ManagedTradeEdit
from backend.managed_live_service import get_live_service

router = APIRouter(prefix="/managed-trades", tags=["managed-live-orders"])


@router.get("")
async def list_managed():
    try:
        return await get_live_service().snapshot()
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/live")
async def create_managed(payload: ManagedTradeRequest):
    try:
        return await get_live_service().create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{trade_id}/edit")
async def edit_managed(trade_id: str, payload: ManagedTradeEdit):
    try:
        return await get_live_service().edit(trade_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{trade_id}/{action}")
async def managed_action(trade_id: str, action: Literal["reconcile", "exit", "release"]):
    try:
        return await get_live_service().action(trade_id, action)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
