"""Fetch current candles and save an auditable open-target swing replay."""
import json
from pathlib import Path

from screener.data_fetcher import DataFetcher, yf
from screener.trailing_swing import analyze


def main():
    folder = Path(__file__).resolve().parent / "index_research_cache"
    folder.mkdir(exist_ok=True)
    results = []
    for symbol, ticker in (("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK")):
        print(f"Loading current {symbol} history...", flush=True)
        raw = yf.download(ticker, period="1y", interval="1h", auto_adjust=False, progress=False, timeout=20)
        data = DataFetcher._normalize_ohlcv(raw, ticker)
        if data is None:
            raise RuntimeError(f"No valid {symbol} data")
        data.to_csv(folder / f"{symbol}_trailing_hourly.csv")
        result = analyze(data, symbol)
        results.append(result)
        print(json.dumps({"symbol": symbol, "status": result["status"], "data_status": result["data_status"],
                          "as_of": result["as_of"], "setup": result["setup"],
                          "trade": {k: result["trade"][k] for k in ("side", "entry", "stop", "remaining")} if result["trade"] else None,
                          "backtest": {k: v for k, v in result["backtest"].items() if k != "recent_trades"}}, allow_nan=False), flush=True)
    (folder / "trailing_report.json").write_text(json.dumps(results, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
