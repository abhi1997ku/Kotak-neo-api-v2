# AI ranking experiment for swing-stock paper trades

Run the offline experiment with the optional dependencies from
[requirements-research.txt](../requirements-research.txt):

```powershell
.venv/Scripts/python.exe -m screener.stock_ai_research
```

The runner uses the real cached history from the existing stock backtest. It
does not contact the broker, modify the paper-account database, or silently
replace the production strategy.

## What is optimized

Two fixed supervised regression models—Extra Trees and histogram gradient
boosting—estimate net outcome in initial-risk units for already-qualified
breakout setups. They filter and rank the candidates; the market filter,
stop, target, gap rules, costs, position sizes, sector cap and six-entry weekly
ceiling are unchanged. Inputs contain only entry-time momentum, trend distance,
volatility, relative strength, volume and market context. Predictions are not
calibrated win probabilities.

An isolated candidate's label uses the production execution engine over its
next ten sessions, after modeled costs. A skipped entry receives zero. A label
is eligible for training only after its entire ten-session window has finished;
missing outcomes are excluded from training, not fabricated.

## Chronological evaluation

- Initial warmup and training precede two 126-session blocks.
- The earlier block selects between two models and two fixed thresholds: four
  trials in total, with no repeated tuning against the later test.
- Objective: validation return minus half the maximum drawdown; variants with
  fewer than 15 closed validation trades are ineligible for selection.
- Each calendar-month prediction block uses a fresh fit on completed earlier
  labels only. This also applies during the later test: parameters and threshold
  stay fixed, while training expands only as outcomes become known.
- The selected configuration is saved before the later test is evaluated.
- Baseline replay must exactly reproduce the original one-year trades and
  ending equity before model comparisons are accepted.

Promotion requires positive validation and test returns, better validation
objective and test return than baseline, no worse test drawdown, at least 30
closed test trades, profit factor above 1.1, and a positive lower bound on the
approximate paired block-bootstrap improvement interval. The research command
only reports eligibility; it never activates a model itself.

## Artifacts

Outputs under `screener/index_research_cache/stock_ai_research/` include:

- `protocol.json`: period boundaries, model list, thresholds and source hashes.
- `frozen_selection.json`: selection made using validation only.
- `report.md` and `results.json`: comparisons, trades, monthly returns and fit audits.
- `research_model.joblib`: research-only trained model with feature order and
  promotion decision; no server code loads it.

Every fit audit records its cutoff and latest training-label date. Cached
prepared examples are keyed by source, strategy and research-code hashes.

## Limits

The current constituent snapshot has survivorship and historical-membership
bias. Daily-bar prices and simplified corporate-action handling can differ from
real execution. The original aggregate backtest was already seen before this
experiment, so the later test is excluded from model selection but is not a
wholly untouched independent dataset. The approximate confidence interval does
not eliminate those limitations. A failed promotion check means further
research or fresh forward evidence is needed; it is not a reason to keep
changing parameters until the same test period looks profitable.

Method reference: [scikit-learn chronological validation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
