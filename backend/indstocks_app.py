"""INDstocks paper dashboard server: no Kotak imports or order routes."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routes.indstocks import router

app = FastAPI(title="INDstocks Paper Research")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"], allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.include_router(router, prefix="/api")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": "indstocks", "orders_enabled": False}
