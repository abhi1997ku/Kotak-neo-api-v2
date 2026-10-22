import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes.auth import router as auth_router
from backend.routes.market import router as market_router
from backend.routes.orders import router as orders_router
from backend.routes.positions import router as positions_router
from backend.routes.watchlist import router as watchlist_router
from backend.routes.screener import router as screener_router
from backend.routes.paper_trading import router as paper_trading_router
from backend.routes.index_swing import router as index_swing_router
from backend.routes.live_swing import router as live_swing_router
from backend.routes.indstocks import router as indstocks_router
from backend.session_manager import get_session_manager

app = FastAPI(title="Kotak Neo Personal Trading Terminal", version="0.1.0")
session_keepalive_task: asyncio.Task | None = None
stock_paper_task: asyncio.Task | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(orders_router, prefix="/api")
app.include_router(positions_router, prefix="/api")
app.include_router(watchlist_router, prefix="/api")
app.include_router(screener_router, prefix="/api")
app.include_router(paper_trading_router, prefix="/api")
app.include_router(index_swing_router, prefix="/api")
app.include_router(live_swing_router, prefix="/api")
app.include_router(indstocks_router, prefix="/api")


@app.on_event("startup")
async def start_session_keepalive():
    global session_keepalive_task, stock_paper_task
    session_keepalive_task = asyncio.create_task(get_session_manager().keepalive_loop())
    from backend.stock_paper_service import service as stock_paper_service
    stock_paper_task = asyncio.create_task(stock_paper_service.daily_loop())


@app.on_event("shutdown")
async def stop_background_services():
    global session_keepalive_task, stock_paper_task
    if stock_paper_task:
        stock_paper_task.cancel()
        await asyncio.gather(stock_paper_task, return_exceptions=True)
        stock_paper_task = None
    if session_keepalive_task:
        session_keepalive_task.cancel()
        await asyncio.gather(session_keepalive_task, return_exceptions=True)
        session_keepalive_task = None
    from backend.live_swing_service import service
    await service.stop()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
