"""Build cash-stock identifiers from provider masters and official constituents."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from backend.watchlist_catalog import get_index_constituents, constituents_source
from backend.intraday_stock_store import ROOT


def build(root=ROOT):
    equities = json.loads((root/"equity_master.json").read_text())
    indices = json.loads((root/"index_master.json").read_text())
    candidates = [r for r in equities if r.get("EXCH") == "NSE" and r.get("SERIES") == "EQ" and r.get("INSTRUMENT_NAME") == "EQUITY"]
    members = get_index_constituents("nifty500")
    stocks, excluded = {}, {}
    for member in members:
        matches = [r for r in candidates if r["TRADING_SYMBOL"] == member["symbol"] and r.get("ISIN") == member["isin"]]
        if len(matches) != 1 or not matches[0]["SECURITY_ID"].isdigit():
            excluded[member["symbol"]] = "Missing or ambiguous symbol/ISIN match"
        else:
            stocks[member["symbol"]] = "NSE_"+matches[0]["SECURITY_ID"]
    benchmarks = [r for r in indices if r.get("EXCH") == "NSE" and r.get("SEGMENT", "").upper() == "NIFTY 500"]
    if len(benchmarks) != 1 or not stocks or len(set(stocks.values())) != len(stocks):
        raise ValueError("Invalid mapping")
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    for file in ("equity_master.json", "index_master.json"):
        if datetime.fromtimestamp((root/file).stat().st_mtime, ZoneInfo("Asia/Kolkata")).date() != now.date():
            raise ValueError("Provider masters must be refreshed today")
    result = dict(as_of=str(now.date()), verified_at=now.isoformat(), benchmark="NIDX_"+benchmarks[0]["SECURITY_ID"],
                  benchmark_name="Nifty 500", stocks=stocks, excluded=excluded,
                  constituent_source=constituents_source("nifty500"), constituent_count=len(members))
    temporary = root/"universe.tmp"
    temporary.write_text(json.dumps(result, indent=2))
    temporary.replace(root/"universe.json")
    print(json.dumps({k: v for k, v in result.items() if k != "stocks"}))
    print("Mapped stocks:", len(stocks))


if __name__ == "__main__":
    build()
