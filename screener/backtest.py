from __future__ import annotations

from typing import Dict, List

import pandas as pd

from screener.feature_engine import compute_setup


def walk_forward_backtest(df: pd.DataFrame, lookback: int = 120) -> Dict[str, float]:
    """A simple walk-forward stub that applies the same signal logic on historical windows.

    This intentionally does not use lookahead data: each scan date sees only data available up to that date.
    """
    results: List[float] = []
    for i in range(lookback, len(df)):
        slice_df = df.iloc[: i + 1].copy()
        setup = compute_setup(slice_df)
        if setup is not None:
            results.append(setup.rr)

    if not results:
        return {"hit_rate": 0.0, "avg_r_multiple": 0.0, "sample_count": 0}

    avg_r = float(sum(results) / len(results))
    hit_rate = float(sum(1 for x in results if x >= 2.0) / len(results))
    return {"hit_rate": hit_rate, "avg_r_multiple": avg_r, "sample_count": len(results)}
