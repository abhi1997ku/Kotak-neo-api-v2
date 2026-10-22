# Selective swing mandate

Superseded by the [expectancy-based swing system](./SWING_EXPECTANCY.md). The 80% gate and fixed targets are no longer active; this document records the previous experiment.

This supersedes the monthly-return goal for new paper entries. Monthly P&L remains a reporting metric, not a quota.

- Primary gross target: 20% above actual entry; 25% is a separately tested alternative, never credited with a 20% hit.
- Structural stop: below the recent five-session low, with distance no greater than 5% at signal and actual opening fill. Skip a setup requiring a wider stop rather than tightening its structural level arbitrarily.
- Horizon: 60 trading sessions (approximately three months).
- Probability event: chosen target reached before the stop within the horizon. Stop wins daily-bar ambiguity. Profitable time exits are still failures for this event.
- Entry gate: strictly greater than 80% validated probability. Ranking points are not probabilities. Current status is UNVALIDATED, so no new entries are authorized.
- Planned account risk stays at the lower of INR 750 or 0.75% of equity including modeled costs; size cap 25% equity, four open positions, two per sector, six weekly entries maximum. No minimum quota or leverage. Gaps/costs can exceed a 5% gross stop distance.

Existing trend, liquidity, volume and confirmed-breakout filters provide research candidates only. Every candidate still needs evidence for the new, much harder outcome. The active service uses selective mode; the legacy default remains available solely for reproducible historical comparisons. Existing filled positions retain their original rules. Previous queued ideas are cancelled, with journal entries, on the next successful scan. History is not erased.

The scanner does not currently load any model artifact. It displays “unvalidated” and holds candidates on the watchlist. This is intentional: no earlier research established 80% target-hit probability. Building a UI that displays 80% from an arbitrary score would not meet the mandate.

Run `.venv/Scripts/python.exe -m screener.stock_selective_research` to evaluate the existing real historical feature cache. The cache source hash is verified and old regression outcomes discarded. New 20%/25% labels use the production paper execution engine. Full setup outcomes, skipped fills, censored signals and model audits are stored under [research results](../screener/index_research_cache/stock_selective_research/).

Model protocol: fixed standardized logistic classifier, then sigmoid calibration on a later block. Training outcomes must completely mature before calibration; calibration outcomes must mature before later testing. Same-symbol overlapping 60-session examples are removed. At least 100 training, 50 calibration and 30 later examples, with both classes represented, are required to fit. No tuning of targets, model or probability threshold on the later block.

Before integrating a probability model, require at least 100 resolved predictions above the threshold, assess calibration and approximate lower confidence bounds, and collect independent forward paper evidence. Market correlation, current-membership survivorship bias and repeated inspection of this historical period prevent interpreting a simple binomial bound as proof of future accuracy. No strategy is activated by the research script.

See [probability calibration documentation](https://scikit-learn.org/stable/modules/calibration.html) and [stop execution limitations](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-15).
