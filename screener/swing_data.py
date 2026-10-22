"""Cached daily data, using the project's existing Yahoo provider. No broker API."""
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path

from screener.stock_paper import IST

BENCHMARK = "^CRSLDX"
ROOT = Path(__file__).resolve().parent / "index_research_cache" / "swing_expectancy"
CACHE = ROOT / "daily.json.gz"


def load(path=CACHE):
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    seed = ROOT.parent / "stock_paper_backtest" / "source_history.json.gz"
    if seed.exists():
        with gzip.open(seed, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        data["seeded_from_previous_research"] = True
        return data
    return {"frames": {}, "members": [], "fetched_at": None}


def save(data, path=CACHE):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as stream:
        json.dump(data, stream, allow_nan=False)
    tmp.replace(path)


def merge_bars(old, fresh):
    """Never stitch different split-adjustment bases; request full reload instead."""
    previous = {b["date"]: b for b in old}
    for bar in fresh:
        prior = previous.get(bar["date"])
        if prior and abs(bar["close"] / prior["close"] - 1) > 0.005:
            return None
        if bar.get("split", 0) != (prior or {}).get("split", 0):
            return None
    previous.update({b["date"]: b for b in fresh})
    return [previous[day] for day in sorted(previous)]


def refresh(now=None, extra_symbols=()):
    # Lazy imports avoid a provider/service import cycle.
    from backend.stock_paper_service import download
    from backend.watchlist_catalog import get_index_constituents, constituents_source
    now = now or datetime.now(IST)
    data = load()
    members = get_index_constituents("nifty500")
    source = constituents_source("nifty500")
    if source.startswith("Bundled"):
        try:
            import requests
            from backend.watchlist_catalog import _parse_constituents
            response = requests.get("https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv", timeout=10)
            response.raise_for_status()
            official = _parse_constituents(response.text)
            if len(official) >= 450:
                members = official
                source = "Nifty Indices official constituent file, fetched " + now.date().isoformat()
        except Exception:
            if data.get("universe_source", "").startswith("Nifty Indices official"):
                members, source = data["members"], "Cached " + data["universe_source"]
    symbols = sorted({BENCHMARK, *(m["symbol"] for m in members), *extra_symbols})
    frames = data["frames"]
    failures = []
    # Completed-session freshness is checked against Nifty 500 by the scanner.
    # Persist per-symbol attempt dates to avoid repeating failed downloads all day.
    attempts = data.setdefault("attempts", {})
    request_key = now.date().isoformat() + ("-closed" if (now.hour, now.minute) >= (16, 10) else "-intraday")
    groups = {}
    for symbol in symbols:
        if attempts.get(symbol) == request_key:
            continue
        old = frames.get(symbol, [])
        lookback = 1200 if not old else max(10, (now.date() - datetime.fromisoformat(old[-1]["date"]).date()).days + 7)
        groups.setdefault(min(1200, lookback), []).append(symbol)
    for lookback, group in groups.items():
        print(f"Daily cache: {len(group)} symbols, {lookback} calendar days", flush=True)
        try:
            fresh = download(group, now, history_days=lookback)
        except Exception as exc:
            failures.append(f"Download failed for {len(group)} symbols: {type(exc).__name__}")
            continue
        for symbol in group:
            attempts[symbol] = request_key
            bars = fresh.get(symbol, [])
            if not bars:
                failures.append(f"No refreshed bars: {symbol}")
                continue
            old = frames.get(symbol, [])
            merged = merge_bars(old, bars) if lookback < 1200 else bars
            if merged is None:
                try:
                    merged = download([symbol], now, history_days=1200).get(symbol)
                except Exception:
                    merged = None
                if not merged:
                    # Incompatible historical bases are quarantined, never blended.
                    frames[symbol] = []
                    failures.append(f"Quarantined {symbol}: corporate-action/history revision refresh failed")
                    continue
            frames[symbol] = merged
    data.update(members=members, frames=frames, fetched_at=now.isoformat(),
                universe_source=source, warnings=failures)
    save(data)
    if not frames.get(BENCHMARK):
        raise ValueError("Real Nifty 500 index history unavailable; no Nifty 50 proxy is substituted")
    return data


if __name__ == "__main__":
    data = refresh()
    print(json.dumps({"members": len(data["members"]), "benchmark_bars": len(data["frames"].get(BENCHMARK, [])), "warnings": data["warnings"]}), flush=True)
