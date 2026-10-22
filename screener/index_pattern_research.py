"""Run reproducible local chart research: python -m screener.index_pattern_research.

Raw downloaded candles and the full trade ledger are cached locally so the
reported experiment can be inspected without repeatedly querying Yahoo.
"""
import json
import argparse
from pathlib import Path
import pandas as pd

from screener.data_fetcher import DataFetcher, yf
from screener.index_pattern import analyze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cached", action="store_true", help="Reuse downloaded candles for reproducible research")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent / "index_research_cache"
    folder.mkdir(exist_ok=True)
    reports = []
    for symbol, ticker in (("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK")):
        source = folder / f"{symbol}_hourly.csv"
        if args.cached:
            data = pd.read_csv(source, index_col=0)
            data.index = pd.to_datetime(data.index, utc=True)
        else:
            print(f"Downloading {symbol} hourly candles...", flush=True)
            if yf is None:
                raise RuntimeError("yfinance is unavailable")
            raw = yf.download(ticker, period="2y", interval="1h", auto_adjust=False, progress=False, timeout=20)
            data = DataFetcher._normalize_ohlcv(raw, ticker)
        if data is None:
            print(f"{symbol}: historical data unavailable", flush=True)
            reports.append({"symbol": symbol, "error": "Historical data unavailable"})
            continue
        if not args.cached:
            data.to_csv(source)
        report = {"symbol": symbol, **analyze(data)}
        reports.append(report)
        print(json.dumps({"symbol": symbol, "status": report["status"],
                          "test": {k: v for k, v in report["backtest"].items() if k != "trades"},
                          "validation_passed": report["model"]["validation_passed"],
                          "threshold": report["model"]["threshold"]}), flush=True)
    (folder / "report.json").write_text(json.dumps(reports, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Research saved to {folder}", flush=True)


if __name__ == "__main__":
    main()
