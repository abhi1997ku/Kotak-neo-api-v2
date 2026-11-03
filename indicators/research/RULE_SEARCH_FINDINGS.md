# Rule search results

Tested 736 explicit candidate rules against saved completed-candle-proxy observations. Families: EMA trend/price relationships, Supertrend-like ATR trails with recovery filters, RSI state machines, rolling high/low breakouts, EMA high/low channels, and channel rules requiring consecutive confirming closes.

No source code was recovered. This is empirical model comparison. Agreement is not evidence that the original uses the same calculations.

## Selection and validation

- Selection data: November 3 BANKNIFTY 55000 Call, 10-second bars from 09:15 to before 12:30. Select by macro-F1 across bearish, recovery, bullish; raw accuracy alone can conceal failures on rare recovery states.
- Freeze the selected parameters and score November 3 afternoon at 10s, 1m, and 5m.
- Candidate families were expanded after inspecting the initial same-day result. These same-day comparisons are exploratory validation, not a pristine one-shot holdout.
- After final selection, score November 2 NIFTY 23150 Call at 10s without retuning. Exclude the first 300 observed consecutive bars after startup or a data gap to reduce EMA warm-up uncertainty. External evaluation covers 944 bars.
- Preceding captured history is used as warm-up, never future observations. Original state and signal labels are never used as prediction inputs. Exact EMA initialization remains uncertain, especially for the short 5m history.

## Best candidate in this bounded search

1. Form an upper EMA(high, 100) and lower EMA(low, 100).
2. Evaluate the **previous** candle's close against its channel.
3. Below the lower channel: bearish; reset the confirmation counter.
4. Above the upper channel: count consecutive closes; recovery until the fourth qualifying close, then bullish.
5. Inside the channel: retain bullish if already bullish; otherwise return to bearish and reset the counter.

This is the selected hypothesis, not an established description of TRENDIKATERR.

| Evaluation sample | State agreement | State macro-F1 | Exact BUY matches | Exact SELL matches |
| --- | ---: | ---: | ---: | ---: |
| Nov 3 afternoon, 10s | 95.4% / 1,058 bars | 0.758 | 2 of 8 original | 3 of 7 original |
| Nov 3 afternoon, 1m | 93.2% / 176 bars | 0.629 | 0 of 2 original | 1 of 3 original |
| Nov 3 afternoon, 5m | 100% / 35 bars | 1.000 | No original BUY to test | 1 of 1 original |
| Nov 2 external, 10s | 92.9% / 944 bars | 0.735 | 1 of 9 original | 1 of 8 original |

The 5m sample has no recovery bars and only one SELL, so its 100% state agreement is weak evidence of signal fidelity. The one-minute candidate never predicted recovery in its evaluated sample, despite four original recovery bars.

For marker scoring, the tested hypothesis is BUY on entry into bullish and SELL on entry into bearish. On Nov 3 afternoon 10s this matched only **5 of 15** original markers, generated **25 extra** markers, and missed **10**. On external Nov 2 it matched only **2 of 17**, generated **23 extra**, and missed **15**. High state agreement therefore does not make this a faithful signal replica.

## Deliverables

- [Reproducible search](./fit_indicator_rules.py), [results with confusion matrices and raw input hashes](./rule_search_results.json).
- [Experimental Pine candidate](../trend_channel_candidate.pine), with candidate signals **hidden by default**. Exact match to original initialization, signal timing, actions, and backgrounds is not claimed. Pine compilation remains unverified because no authenticated Pine Editor is available here.
- Four local unit tests passed for channel state progression, label-independent predictions, false/missed marker counts, and EMA arithmetic. These validate the Python implementation, not original-indicator equivalence.

The older [price/slope prototype](../trend_ribbon_reconstruction.pine) remains unvalidated and is superseded as the strongest tested candidate. The EMA(close, 11) plot formula is well supported; the candidate color remains approximate.

## TTC and next unresolved work

Today's base 811.05 equals the previous session's last recorded close in all three timeframes. This supports a previous-close reference for this one contract/day, but earlier samples have intraday base changes. The relationship between the exposed base and the visible half-integer-square levels still needs direct verification. Do not replace that uncertainty with a guessed automatic rule.

The main unresolved bottleneck is **entry confirmation**, followed by recovery-state transitions and TTC grid/base behavior. The present evidence is sufficient for an approximate research visualization, but does not establish an exact VIP replica. Further work should explain individual marker mismatches and test a newly frozen rule on another session or put contract. No profitability assessment or trade execution was performed.
