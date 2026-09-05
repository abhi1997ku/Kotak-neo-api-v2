from __future__ import annotations

from dataclasses import dataclass
from typing import List
import sys
import logging

import pandas as pd

try:
    from screener.feature_engine import compute_setup
    from screener.universe_loader import load_watchlist
    from screener.data_fetcher import fetch_data_batch
except ModuleNotFoundError:  # pragma: no cover
    from feature_engine import compute_setup
    from universe_loader import load_watchlist
    from data_fetcher import fetch_data_batch

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class ScreenerResult:
    symbol: str
    setup_type: str
    entry: float
    stop: float
    target: float
    rr: float
    suggested_qty: int
    rationale: str


def rank_candidates(frames: dict[str, pd.DataFrame], min_rr: float = 2.0) -> List[ScreenerResult]:
    """Rank candidates from a symbol->OHLCV map using the same rules as the feature engine."""
    results: List[ScreenerResult] = []
    for symbol, df in frames.items():
        setup = compute_setup(df, min_rr=min_rr)
        if setup is None:
            continue
        results.append(
            ScreenerResult(
                symbol=symbol,
                setup_type=setup.setup_type,
                entry=setup.entry,
                stop=setup.stop,
                target=setup.target,
                rr=setup.rr,
                suggested_qty=setup.suggested_qty,
                rationale=setup.rationale,
            )
        )
    return sorted(results, key=lambda x: x.rr, reverse=True)


def synthetic_universe() -> dict[str, pd.DataFrame]:
    """Build a synthetic universe with clear consolidation -> sharp breakout patterns.
    
    Pattern: 80 days tight consolidation, then 20-day breakout with volume surge on final days.
    """
    universe = {}
    for symbol_idx, symbol in enumerate(load_watchlist()):
        dates = pd.date_range(start="2024-01-01", periods=100, freq="D")
        
        base_price = 100.0 + (symbol_idx % 5) * 10
        
        # Days 0-79: TIGHT CONSOLIDATION
        consol_close = base_price + ((pd.Series(range(80)) % 3 - 1.5) * 0.25)
        consol_high = consol_close + 0.6
        consol_low = consol_close - 0.6
        consol_open = consol_close
        consol_vol = 1000 + (pd.Series(range(80)) % 50)  # ~1000-1050
        
        # Days 80-99: BREAKOUT with acceleration
        # Start well above consolidation, then move up 38+ points over 20 days (2 points/day)
        breakout_start = base_price + 50.0  # big gap up
        breakout_close = breakout_start + pd.Series(range(20)) * 2.0
        breakout_high = breakout_close + 1.0
        breakout_low = breakout_close - 0.3
        breakout_open = breakout_close - 1.0
        
        # Volume: gradual increase then final surge
        breakout_vol = pd.Series([5000] * 15 + [15000] * 5)  # last 5 days have massive volume
        
        all_close = pd.concat([consol_close, breakout_close], ignore_index=True)
        all_high = pd.concat([consol_high, breakout_high], ignore_index=True)
        all_low = pd.concat([consol_low, breakout_low], ignore_index=True)
        all_open = pd.concat([consol_open, breakout_open], ignore_index=True)
        all_vol = pd.concat([consol_vol, breakout_vol], ignore_index=True)
        
        df = pd.DataFrame({
            "Open": all_open.values,
            "High": all_high.values,
            "Low": all_low.values,
            "Close": all_close.values,
            "Volume": all_vol.values,
        }, index=dates)
        universe[symbol] = df
    return universe


def format_report(candidates: List[ScreenerResult]) -> str:
    """Format ranked candidates into a readable CLI table."""
    if not candidates:
        return "No candidates passed the screening filters (min RR >= 2.0)."
    
    lines = ["\n=== SWING TRADE CANDIDATES ==="]
    lines.append(f"Found {len(candidates)} candidate(s) ranked by Risk:Reward ratio\n")
    lines.append(f"{'Symbol':<12} {'Setup':<12} {'Entry':<10} {'Stop':<10} {'Target':<10} {'R:R':<8} {'Qty':<8}")
    lines.append("-" * 80)
    
    for candidate in candidates:
        lines.append(
            f"{candidate.symbol:<12} {candidate.setup_type:<12} "
            f"{candidate.entry:<10.2f} {candidate.stop:<10.2f} {candidate.target:<10.2f} "
            f"{candidate.rr:<8.2f} {candidate.suggested_qty:<8d}"
        )
    
    lines.append("-" * 80)
    return "\n".join(lines)


def real_universe(days: int = 100) -> dict[str, pd.DataFrame]:
    """Fetch real market data from Yahoo Finance for the universe."""
    logger.info("Fetching real market data from Yahoo Finance...")
    
    symbols = load_watchlist()
    logger.info(f"Scanning {len(symbols)} symbols: {', '.join(symbols)}")
    
    data_dict = fetch_data_batch(symbols, days=days)
    
    if not data_dict:
        logger.warning("No data fetched. Falling back to synthetic data.")
        return synthetic_universe()
    
    logger.info(f"Successfully fetched {len(data_dict)} symbols")
    return data_dict


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Swing trade screener")
    parser.add_argument(
        "--mode",
        choices=["real", "synthetic"],
        default="real",
        help="Data source: real (Yahoo Finance) or synthetic (for testing)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=100,
        help="Number of days of history to fetch (default: 100)"
    )
    parser.add_argument(
        "--min-rr",
        type=float,
        default=2.0,
        help="Minimum risk:reward ratio (default: 2.0)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "="*80)
    print(f"SWING TRADE SCREENER - Mode: {args.mode.upper()}")
    print("="*80)
    
    if args.mode == "real":
        universe = real_universe(days=args.days)
    else:
        universe = synthetic_universe()
    
    candidates = rank_candidates(universe, min_rr=args.min_rr)
    report = format_report(candidates)
    print(report)
