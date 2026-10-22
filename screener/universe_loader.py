from __future__ import annotations

from pathlib import Path
from typing import List

try:
    from backend.watchlist_catalog import get_index_constituents
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend.watchlist_catalog import get_index_constituents


def load_universe_metadata(
    path: str | Path | None = None,
    universe: str = "nifty500",
) -> list[dict[str, str]]:
    """Load a custom scanner list or current Nifty 50/500 with industry labels."""
    target = Path(path) if path else Path(__file__).resolve().parent / "watchlist.txt"
    if target.exists():
        with target.open("r", encoding="utf-8") as file:
            return [
                {"symbol": line.strip().upper(), "name": line.strip().upper(), "industry": "Unknown"}
                for line in file
                if line.strip() and not line.lstrip().startswith("#")
            ]

    normalized_universe = str(universe or "nifty500").strip().lower().replace(" ", "")
    if normalized_universe not in {"nifty50", "nifty500"}:
        raise ValueError("universe must be 'nifty50' or 'nifty500'")
    constituents = get_index_constituents(normalized_universe)
    if not constituents:
        raise RuntimeError(f"The {normalized_universe.upper()} constituent list is empty")
    return constituents


def load_watchlist(path: str | Path | None = None) -> List[str]:
    """Load a configured local watchlist, or fall back to Nifty 50 members."""
    items = [item["symbol"] for item in load_universe_metadata(path, universe="nifty50")]
    return items or ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]
