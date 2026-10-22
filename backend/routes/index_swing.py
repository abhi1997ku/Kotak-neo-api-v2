import logging
import copy
import threading
import time
import json
from pathlib import Path
import pandas as pd
from fastapi import APIRouter, HTTPException
from screener.data_fetcher import DataFetcher, yf
from screener.smooth_trailing import analyze, minute_bars
from screener.trade_journal import save_trades, export_records
from fastapi.responses import Response

router = APIRouter(prefix="/index-swing", tags=["index-swing"])
logger = logging.getLogger(__name__)
_cache = {}
_hourly_cache = {}
_minute_dir = Path(__file__).resolve().parents[2] / "screener" / "index_research_cache" / "smooth_minutes"
_scan_lock = threading.Lock()
_research_report = Path(__file__).resolve().parents[2] / "screener" / "index_research_cache" / "latest_search.json"


@router.get("/history/export")
def history_export(closed_only: bool = False):
    records = export_records(closed_only=closed_only)
    body = "".join(json.dumps(row, allow_nan=False)+"\n" for row in records)
    return Response(body, media_type="application/x-ndjson",
                    headers={"Content-Disposition": 'attachment; filename="swing-trades.jsonl"'})


@router.get("/experiments")
def experiments():
    """Read completed offline experiments; this endpoint never trains or trades."""
    if not _research_report.exists():
        return {"status": "NOT_RUN", "results": []}
    try:
        payload = json.loads(_research_report.read_text(encoding="utf-8"))
        return {"status": "COMPLETE", "results": payload["results"]}
    except (OSError, ValueError, KeyError):
        raise HTTPException(status_code=503, detail="Saved research report could not be read")


@router.get("/scan")
def scan(trail_minutes: int = 1):
    if trail_minutes not in (1, 5):
        raise HTTPException(status_code=400, detail="Trailing interval must be 1 or 5 minutes")
    # The route is synchronous: serialize model builds and share a short cache.
    with _scan_lock:
        if _cache and _cache.get("interval") == trail_minutes and time.monotonic() - _cache["at"] < 30:
            return copy.deepcopy(_cache["result"])
        result = _scan(trail_minutes)
        if all(row["status"] != "UNAVAILABLE" for row in result["results"]):
            _cache.update(at=time.monotonic(), result=result, interval=trail_minutes)
        return copy.deepcopy(result)


def _minutes(name, ticker):
    path = _minute_dir / f"{name}.csv"
    saved = None
    if path.exists():
        saved = pd.read_csv(path, index_col=0)
        saved.index = pd.to_datetime(saved.index, utc=True)
        saved = minute_bars(saved)
    raw = yf.download(ticker, period="7d", interval="1m", ignore_tz=False, auto_adjust=False, progress=False, timeout=20)
    fresh = DataFetcher._normalize_ohlcv(raw, ticker)
    if fresh is None or fresh.empty:
        if saved is not None:
            return saved, "Download unavailable; using saved minute candles."
        raise ValueError("One-minute market data unavailable")
    fresh = minute_bars(fresh)
    merged = minute_bars(pd.concat([saved, fresh]) if saved is not None else fresh)
    _minute_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    merged.to_csv(temporary)
    temporary.replace(path)
    return merged, "Real one-minute candles, accumulated locally across refreshes."


def _scan(trail_minutes=1):
    results = []
    for name, ticker in [("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK")]:
        try:
            if yf is None:
                raise ValueError("Install yfinance to retrieve hourly history")
            cached = _hourly_cache.get(name)
            if cached and time.monotonic()-cached[0] < 300:
                data = cached[1]
            else:
                raw = yf.download(ticker, period="1y", interval="1h", auto_adjust=False, progress=False, timeout=20)
                data = DataFetcher._normalize_ohlcv(raw, ticker)
                if data is not None:
                    _hourly_cache[name] = (time.monotonic(), data)
            if data is None:
                raise ValueError("Hourly market data unavailable")
            minutes, source = _minutes(name, ticker)
            report = analyze(data, minutes, name, trail_minutes, journal=save_trades)
            report["data_source"] = source
            results.append(report)
        except ValueError as exc:
            results.append({"symbol": name, "status": "UNAVAILABLE", "reason": str(exc)})
        except Exception:
            logger.exception("Index swing data failed for %s", name)
            results.append({"symbol": name, "status": "UNAVAILABLE", "reason": "Could not retrieve or save sufficient valid hourly and minute market data. Retry later."})
    return {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "results": results}
