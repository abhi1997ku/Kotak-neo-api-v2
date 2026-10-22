# Expanded index-model search

The offline [experiment runner](../screener/index_experiments.py) explores
model, feature, risk, holding-period and score-threshold choices. It preserves
every run and does not change the active trading agent automatically.

## Fixed search space

Per index, the full run evaluates 1,152 configurations:

- Feature sets: 12 custom candle features; those plus 16 chart-pattern
  descriptors; or all of those plus six conventional indicator descriptors.
- Models: histogram gradient boosting with 7 or 15 leaves; extra trees;
  random forest. Tree settings and random seeds are fixed in source.
- Stop budget multipliers: 0.50, 0.75, 1.00 and 1.25. A 0.05% price floor
  prevents vanishing stops. Each target remains exactly three times its stop
  distance after rounding.
- Holding limits: 21, 42 or 70 session bars, including closing 15-minute bars.
- Classifier thresholds: 20%, 30%, 40%, 50%, 60%, 70%, 80%, 90%.

Chart descriptors include engulfing candles, wick rejection, inside/outside
bars, channel breaks, separated-window double-top/bottom geometry,
head-and-shoulders geometry, compression and slope changes. These approximate
geometric patterns rather than certify a visual pattern. All windows end at
the signal candle or earlier. Hybrid features add RSI, EMA separation/slope,
range z-score and longer price displacement. Index volume is not fabricated.

Features need 96 full candles of contiguous regular-session context after a
detected missing-bar boundary. Whole missing days cannot be classified as
missing data versus exchange holidays without an exchange calendar.

## Selection and evaluation

The data is chronological; it is never randomly shuffled. Three expanding
training windows forecast validation periods at 35–45%, 45–57.5% and
57.5–70% of history. Every training label's full possible outcome horizon
must finish before its validation period starts. Validation labels must also
finish inside their period, so trades do not leak across fold boundaries.

Candidates require at least 30 aggregate validation trades and five in each
period to qualify as supported. Supported trials rank above tiny samples;
within that group selection maximizes the lower end of the approximate 95%
Wilson interval for full-target hit rate, then mean net R. These intervals
are descriptive, not corrected for multiple model comparisons or serial
dependence.

The selected configuration is written to disk **before** evaluating the final
30% of history. The selected model is fitted on pre-test observations with
matured labels, and its final-period results cannot change the winner. The
runner does not choose an alternative after seeing a poor test result.

This separates model selection from evaluation, as discussed in the
[scikit-learn nested-validation documentation](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html).
The split logic here is specifically chronological and purged. Boosting uses
`early_stopping=False` to avoid the estimator's internal random validation
split; see the [estimator documentation](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html).

The existing history was inspected during previous strategy development.
Therefore the final period is withheld from this search's selection, but it
is still an **exploratory historical result**, not fresh prospective evidence.
Running identical simulations repeatedly does not create independent trials.

## Metrics and goal definition

Accuracy means **3R target reached before stop or timeout**, divided by every
completed trade. Profitable timeouts are reported separately and are not
counted as target wins. Idle/non-trade predictions do not inflate accuracy.

The report includes net R, mean and median R, profit factor, average winner
and loser, profitable-exit rate, closed-trade drawdown, longest losing streak,
holding time, signal coverage, long/short target rates, monthly results,
confidence intervals, both-direction Brier score, and net R when assumed
costs double from 4 to 8 bps round trip. Intratrade drawdown and actual
derivative execution costs are not estimated. Profit factor is undefined
when there are no losing trades, rather than serialized as infinity.

An observed goal requires over 80% target hits and at least 30 final-period
trades. A stricter historical evidence flag requires at least 100 trades, a
Wilson lower bound above 80%, positive returns under doubled costs, supported
validation samples and positive returns in every validation period. Neither
flag verifies future accuracy. Unsupported high-rate trials are counted and
remain in the full ledger instead of being promoted.

## Run and inspect

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-research.txt
.venv/Scripts/python.exe -m screener.index_experiments
```

This uses the candles already saved by the
[original research downloader](../screener/index_pattern_research.py). Use
`--quick` for a small integration run or `--symbols NIFTY` to restrict scope.
Scikit-learn is an optional offline research dependency; the active server's
analog scan does not import it.

Each run gets a new timestamped directory under the ignored local
`screener/index_research_cache` directory. It contains the frozen manifest
(source/data hashes, dependency versions, settings), complete per-index JSONL
trial logs, the selected configurations, final-period trade ledgers, and
summary metrics. Previous run directories remain available. A convenience
pointer/report is saved as `latest_search.json` after both indices finish.

The dashboard's **Load research results** button reads the completed report
through `GET /api/index-swing/experiments`. It does not run a training job or
submit an order. During a new run it continues to show the last completed
report; the manifest timestamp identifies the corresponding experiment.
