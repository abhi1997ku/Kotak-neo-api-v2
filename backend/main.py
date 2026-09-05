from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes.auth import router as auth_router
from backend.routes.market import router as market_router
from backend.routes.orders import router as orders_router
from backend.routes.positions import router as positions_router
from backend.routes.watchlist import router as watchlist_router
from backend.routes.screener import router as screener_router

app = FastAPI(title="Kotak Neo Personal Trading Terminal", version="0.1.0")

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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
