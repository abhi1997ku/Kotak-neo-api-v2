"""Optional standalone server: stock paper features without any broker services.

Run: python -m uvicorn backend.stock_paper_app:app --host 127.0.0.1 --port 8002
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes.screener import router
from backend.stock_paper_service import service


@asynccontextmanager
async def lifespan(app):
    worker = asyncio.create_task(service.daily_loop())
    try:
        yield
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title="Stock Paper Portfolio", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:5174", "http://127.0.0.1:5174",
], allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok", "mode": "stock-paper-only", "broker_connected": False}
