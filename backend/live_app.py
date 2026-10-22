"""Dedicated live terminal process. No paper/research imports or background jobs."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routes.auth import router as auth_router
from backend.routes.market import router as market_router
from backend.routes.orders import router as orders_router
from backend.routes.positions import router as positions_router
from backend.routes.watchlist import router as watchlist_router
from backend.session_manager import get_session_manager
from backend.routes.managed_trades import router as managed_trades_router
from backend.managed_live_service import get_live_service


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(get_session_manager().keepalive_loop())
    preview_task = asyncio.create_task(get_live_service().run())
    try:
        yield
    finally:
        task.cancel()
        preview_task.cancel()
        await asyncio.gather(task, preview_task, return_exceptions=True)


app = FastAPI(title="Kotak Neo Live Trading", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5173, 5174)],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)
for router in (auth_router, market_router, orders_router, positions_router, watchlist_router, managed_trades_router):
    app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok", "workspace": "live"}
