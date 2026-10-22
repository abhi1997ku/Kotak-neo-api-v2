# Hourly custom candle-pattern agent

The active dashboard now uses the [open-target trailing swing agent](./TRAILING_SWING_AGENT.md).
This document describes the earlier fixed-target research version.

The earlier Nifty/Bank Nifty panel called the supervised pattern model in
[index_pattern.py](../screener/index_pattern.py), through
[the scan route](../backend/routes/index_swing.py), which has since been switched to the trailing agent. The previous EMA/RSI model
in [index_swing.py](../screener/index_swing.py) remains as historical code and
is no longer used by this route. No orders are placed.

The later [expanded model search](./INDEX_MODEL_EXPERIMENTS.md) compares tree
models, additional chart patterns and optional conventional indicators across
multiple validation periods. Its results are available through **Load research
results** in the same dashboard panel. Offline experiments do not automatically
replace this active analog model.

## What the model learns

Each index is fitted separately using completed, timezone-aware hourly OHLC
candles. There is no screenshot recognition or generative model: this is a
supervised nearest-neighbour model built with NumPy and pandas. Training
outcomes determine feature weights, while train-only normalization makes
chart shapes comparable across price levels.

The custom features describe raw candle structure rather than computing RSI,
EMA, MACD, or ATR:

| Feature | Definition |
| --- | --- |
| Body pressure | Six-bar sum of signed bodies divided by sum of candle ranges |
| Wick rejection | Six-bar lower-minus-upper wick sum divided by total range |
| Price-path efficiency | Six-bar displacement divided by total absolute close-to-close travel |
| Range compression | Three-bar average range relative to twelve-bar average range |
| Failed upper/lower breaks | Three-bar frequency of breaks beyond the preceding twelve-bar extreme that close back inside |
| Price acceptance | Three-bar average close location relative to the preceding twelve-bar price envelope |
| Gap response | Three-bar average opening gap, signed by candle body direction, normalized by the risk budget |
| Path shape | One-, three-, six-, and twelve-bar displacement normalized by the risk budget |

The stop budget is the rolling 65th percentile of the past 24 three-hour
high-low envelopes, with a floor of 0.1% of price. At entry the stop is one
budget away and the target is exactly three budgets away, after price rounding.
These formulas are engineered features, not a claim that the underlying
price-action concepts have never been used elsewhere.

The training target is the realized long-versus-short net-R advantage, clipped
when learning feature weights to limit extreme gaps. Weights are a base weight
plus absolute training correlation with that target, then normalized. In the
weighted feature space the model selects up to 48 nearest historical patterns,
spaced by at least six full bars to reduce clustering. Their target-hit rate
and mean net R provide direction scores. The score is an empirical analog
frequency, **not a calibrated forecast probability**. Neighbours' future
windows can still overlap; they are not independent samples.

## Validation and execution assumptions

1. First 50% of hourly history: initial training.
2. Next 20%: choose a score threshold using validation mean net R, requiring
   at least 12 validation trades. A failed experiment remains visible.
3. Final 30%: evaluate the selected threshold with a model fitted on the
   pre-test data only. The complete possible label horizon must end before
   each training cutoff. The final model remains frozen for the latest scan.

Chronological splitting and separation between training and future data follow
the principle described in the
[scikit-learn time-series split documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
The implementation does not depend on scikit-learn.

The thresholds searched on validation are 20%, 25%, 30%, 35%, 40%, 45%, 50%,
60%, 70%, and 80%. Selection does not maximize test-period returns. A signal
also requires positive analog mean net R. If no threshold has enough
validation trades, the 20% threshold is shown diagnostically and the validation
gate fails. Live research setups require positive validation results and at
least 30 test trades with positive total net R. This is a minimum research
gate, not evidence of 80% accuracy or statistical certification.

- Signals are formed only after a full hourly candle completes. Simulation
  enters at the next session bar's open, including the closing 15-minute bar.
- Only one simulated position per index may be open at a time.
- Adverse open gaps fill at the open. Favourable target gaps fill at the target.
  If both levels are crossed intrabar, the stop wins unless the open already
  established the target fill.
- Unresolved positions exit after 21 session bars, approximately three regular
  sessions. Time exits can be profitable but **do not count as 3R target hits**.
- Costs/slippage are assumed to be 4 basis points of entry notional round trip,
  converted to R using the stop budget. These are illustrative index-level
  assumptions, not actual futures/option execution costs.
- Recent patterns without a complete future horizon are excluded and counted
  separately. They are not silently labeled losses or wins.
- Incomplete current candles and partial closing bars never create features.
  The closing segment still participates in fills and exits.
- Missing regular-session bar boundaries invalidate affected feature windows
  and labels. Whole missing sessions cannot be distinguished from holidays
  without an exchange calendar. Special sessions outside regular hours are
  excluded; current results report detected gaps.
- Quotes older than two hours after the last full candle's close suppress
  setups, including outside market hours. Scan responses cache for five minutes.

The interface shows target-hit rate, profitable-trade count, timeouts, net R,
maximum closed-trade-equity drawdown, an approximate Wilson interval, model
weights, matched historical examples, and the complete test trade ledger.
Intratrade mark-to-market drawdown is not estimated.

This is a development research experiment on reused historical data. The test
partition is withheld from fitting and threshold selection within each run,
but it is not a fresh prospective trial. Repeated strategy development on this
history can introduce selection bias. No code path claims future accuracy.

## Reproduce and inspect

Development snapshot, 5 October 2026, with incomplete chart windows excluded:

| Index | Test trades | Full 3R target hits | Target-hit rate | Profitable trades including time exits | Net R after assumed costs |
| --- | ---: | ---: | ---: | ---: | ---: |
| Nifty | 24 | 7 | 29.17% | 15 | +18.688 |
| Bank Nifty | 8 | 1 | 12.50% | 3 | +1.836 |

The test period begins 26 February 2026. Both indices failed the validation
gate and have fewer than 30 test trades, so neither is enabled for signals.
Four missing regular-session boundaries were detected per index, and affected
windows were excluded. These development results do not demonstrate the
requested 80% accuracy or establish improvement over the old strategy under
identical execution assumptions.

Run from the repository root:

```powershell
.venv/Scripts/python.exe -m screener.index_pattern_research
.venv/Scripts/python.exe -m screener.index_pattern_research --cached
.venv/Scripts/python.exe -m pytest tests/test_index_pattern.py tests/test_index_swing.py -q
```

The first command downloads two years of Yahoo Finance hourly index candles.
The second reuses those exact candles. Both write a full JSON report and trade
ledger to the ignored local `screener/index_research_cache` directory. An
unavailable data source or insufficient valid training history produces an
explicit error, never fabricated candles or results.
