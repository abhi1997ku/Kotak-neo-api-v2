from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Set


class ConnectionManager:
    """Simple WebSocket fan-out manager for frontend updates.

    The real deployment should subscribe to broker feeds on the backend and push
    normalized deltas to all connected browsers. This stub keeps the API contract
    clear before the real feed integration is added.
    """

    def __init__(self) -> None:
        self.connections: Set[Any] = set()
        self.subscriptions: Dict[str, List[str]] = defaultdict(list)

    async def connect(self, websocket: Any) -> None:
        self.connections.add(websocket)

    async def disconnect(self, websocket: Any) -> None:
        self.connections.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        for ws in list(self.connections):
            await ws.send_json(message)

    async def subscribe(self, websocket: Any, channel: str) -> None:
        self.subscriptions[channel].append(str(id(websocket)))

    async def unsubscribe(self, websocket: Any, channel: str) -> None:
        self.subscriptions[channel] = [
            cid for cid in self.subscriptions.get(channel, []) if cid != str(id(websocket))
        ]

    async def push_quote_update(self, symbol: str, payload: Dict[str, Any]) -> None:
        await self.broadcast({"type": "quote", "symbol": symbol, "payload": payload})

    async def push_order_update(self, payload: Dict[str, Any]) -> None:
        await self.broadcast({"type": "order", "payload": payload})
