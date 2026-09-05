from __future__ import annotations

from pathlib import Path
from typing import List


def load_watchlist(path: str | Path | None = None) -> List[str]:
    """Load a configurable watchlist file.

    This is a placeholder for a small local CSV / text watchlist that can be
    expanded later to the Nifty 500 or a custom universe.
    """
    target = Path(path) if path else Path(__file__).resolve().parent / "watchlist.txt"
    if not target.exists():
        return ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]

    with target.open("r", encoding="utf-8") as file:
        items = [line.strip() for line in file if line.strip() and not line.startswith("#")]
    return items or ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]
