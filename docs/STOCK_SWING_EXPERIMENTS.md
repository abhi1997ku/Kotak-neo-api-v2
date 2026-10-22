# Stock swing entry/exit research

Run `.venv/Scripts/python.exe -m screener.stock_swing_experiments` from the repository root.
Run `.venv/Scripts/python.exe -m pytest tests/test_stock_paper.py tests/test_stock_paper_backtest.py tests/test_stock_ai_research.py tests/test_stock_swing_experiments.py -q` to verify execution and research invariants.

Uses the existing real Yahoo daily-bar cache produced by [the historical runner](../screener/stock_paper_backtest.py). No downloads, broker access, paper account writes, or scanner strategy activation. Outputs are under [the experiment folder](../screener/index_research_cache/stock_swing_experiments/), including a report and full trade/order/equity history.

Nine fixed combinations are tested:

| Entry | Confirmation |
|---|---|
| Existing breakout | Unchanged production strategy |
| Trend pullback | Yesterday touches its 20-session mean within 1%, closes no more than 2% below it; prior close above that mean; today closes above yesterday's high and today's mean, no more than 1.5 ATR above the mean |
| Breakout retest | Breakout above its previous 20-session high 2–10 sessions ago; a subsequent low touches within 1% of that level; intervening closes no more than 2% below it; today closes above the level and yesterday's high, within one ATR of the level |

Both new entries require price above SMA50 above SMA200, Nifty above SMA50, price >= INR 50, mean turnover >= INR 10 crore, relative 20-session performance >=2 percentage points, volume >= prior 20-session mean and a close in the top 30% of the daily range. Stops sit 0.1 ATR below the last five lows, with 2–8% signal stop distance. Rank uses the same score formula as the original. These are explicit hypotheses, not evidence of profitability.

| Exit | Rules |
|---|---|
| original_10 | Original 3R target and 10-session time exit |
| target8_20 | Target 8% above signal close, up to 20 sessions |
| partial5_trail_30 | Sell half (rounded down) at 5% above actual fill; remainder targets 10% above signal close, or trails 3% below the highest close since partial profit with a gross-entry floor; exit after 30 sessions |

Every entry uses next-session open with existing gap and >=2R-to-final-target checks. Fixed-target variants also require >=2R at signal time. Partial profit lowers blended reward; 2R to the last target is not a blended reward claim. Stops are evaluated before profit levels on ambiguous bars. A raised trail applies only next session. No intraday high is used to retroactively tighten a stop.

Account limits and costs are shared with the production engine. The optional research exit handler is never passed by server callers. Partial proceeds, remaining quantities, split-adjusted trails, dividends and final proceeds reconcile to account equity. A partial exit does not count as another weekly entry.

Select once on the earlier portion of the two-year window, ending immediately before the last 126 sessions: return minus half maximum daily-close drawdown, with at least 30 closed trades. Freeze the selected combination before evaluating the later period. Separate accounts start at INR 100,000 in each window. A full-period selected replay is descriptive and includes selection data. Exact original two-year trade and equity parity is required.

The dataset has already been inspected in previous research, so the later period is not pristine independent validation. Current membership causes survivorship bias. Bar data, corporate-action handling and transaction costs are approximations. No automatic promotion is permitted: forward paper evidence is needed, and neither trade-level 5–10% nor account-level 5% monthly returns is guaranteed.
