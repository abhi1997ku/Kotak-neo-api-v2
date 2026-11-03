"""Explicit read-only cache refresh: python -m backend.intraday_stock_cache.

Requires data/intraday_stocks/universe.json with today's as_of, benchmark code,
and stocks mapping SYMBOL to verified INDstocks cash-equity code. No guessed IDs.
"""
import json
import re
import time
import pandas as pd
from backend.indstocks_data import client, INDstocksError
from backend.intraday_stock_store import ROOT


def refresh(root=ROOT, provider=client, now=None):
    now = pd.Timestamp.now(tz="Asia/Kolkata") if now is None else pd.Timestamp(now)
    mapping = json.loads((root/"universe.json").read_text())
    if mapping["as_of"] != str(now.date()) or not mapping["stocks"]:
        raise ValueError("Today's verified stock mapping required")
    codes = [mapping["benchmark"], *mapping["stocks"].values()]
    if len(set(codes)) != len(codes) or any(not re.fullmatch(r"[A-Z]+_\d+", c) for c in codes):
        raise ValueError("Invalid or duplicate provider identifiers")
    path = root/"candles.json"
    cached = json.loads(path.read_text()) if path.exists() else {}
    previous_mapping = cached.get("mapping", {})
    same_mapping = (previous_mapping.get("stocks") == mapping["stocks"]
                    and previous_mapping.get("benchmark") == mapping["benchmark"])
    prior = cached.get("provider_cache", {}) if same_mapping else {}
    collected = {}
    pages = root/"history"
    pages.mkdir(exist_ok=True)
    for offset in range(0, len(codes), 5):
        batch = codes[offset:offset+5]
        merged, starts = {}, []
        for code in batch:
            checkpoint = pages/(code+".json")
            old = json.loads(checkpoint.read_text()) if checkpoint.exists() else prior.get(code, [])
            merged[code] = {x["ts"]: x for x in old}
            starts.append(pd.Timestamp(max(x["ts"] for x in old), unit="s", tz="UTC")-pd.Timedelta(minutes=10) if old else now.normalize()-pd.Timedelta(days=45))
        start = min(starts)
        while start < now:
            end = min(start+pd.Timedelta(days=7), now)
            for attempt in range(3):
                try:
                    response = provider.history(batch, int(start.timestamp()*1000), int(end.timestamp()*1000))
                    break
                except INDstocksError:
                    if attempt == 2:
                        raise
                    print("Provider request failed; bounded retry", flush=True)
                    time.sleep(5*(attempt+1))
            for code in batch:
                candles = response["data"].get(code, {}).get("candles", [])
                for bar in candles:
                    if bar["ts"]+60 <= now.timestamp():
                        merged[code][bar["ts"]] = bar
            start = end
        for code in batch:
            collected[code] = sorted(merged[code].values(), key=lambda x: x["ts"])
            temporary = pages/(code+".tmp")
            temporary.write_text(json.dumps(collected[code]))
            temporary.replace(pages/(code+".json"))
        print(f"Cached {min(offset+5, len(codes))}/{len(codes)} instruments", flush=True)
    payload = dict(source="INDstocks", universe_as_of=mapping["as_of"], mapping=mapping,
                   fetched_at=now.isoformat(),
                   benchmark=dict(candles=collected[mapping["benchmark"]]),
                   stocks={s: dict(candles=collected[c]) for s, c in mapping["stocks"].items()})
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


if __name__ == "__main__":
    refresh()
