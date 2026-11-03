# Indicator reconstruction research

Latest: [736-rule search and external validation](./research/RULE_SEARCH_FINDINGS.md). The [channel candidate](./trend_channel_candidate.pine) approximates trend states more closely than the initial draft, but its BUY/SELL timing remains a poor match. Candidate signals are hidden by default.

These are independent experimental Pine Script v6 implementations, not recovered source. No accuracy or profitability claim is made. Neither script has been compiled in TradingView. Subsequent [historical research](./research/findings.md) confirms the close EMA(11) ribbon recurrence across 649 consecutive comparisons, but contradicts the draft's state logic. The shared INDstocks page did not expose a Pine editor in its visible UI.

## Usage

In TradingView's Pine Editor, paste each `.pine` file into a separate indicator and add it to the same symbol, exchange, timeframe, session, and candle type as the original. Broker chart integrations may not accept custom Pine scripts.

- [TTC](./ttc_reconstruction.pine): copy the original BASE price into the input. Start with 961 for the observed call chart or 676 for the observed put chart; these bases are visually inferred, not read in full from their clipped labels. Adjust to the exact original base before comparing. Automatic daily base selection is intentionally not guessed. This version draws the current manual grid over calendar days, not separate historical daily grids.
- [Ribbon](./trend_ribbon_reconstruction.pine): length 11, close source by default. Choose Call, Put, or Other explicitly. Its price/slope state logic, recovery classification, signal persistence, bar-close confirmation, and action mapping are hypotheses. The original visibly remained bearish on the call while close (760.35) exceeded ribbon (757.56), so a simple price crossover alone cannot explain it. The implementation adds slope agreement but that is not yet validated.

## Observed evidence

Shared authenticated chart inspected October 31, 2026 IST. BANKNIFTY 23 Nov 54900 call and put, 1-minute standard candles.

TTC inputs: Show Labels, Show Prices, Line Width 1, above/below/base colors, Days to Show 20. Above/below controls are colors, not observed level-count inputs. Its only status-line plot was Dummy with no value. The chart shows blue levels above base and red below.

Visible grid labels include 600, 625, 650, 676, 702, 729, 756, 784, 812, 841, 870, 900, 930, 992, 1024, 1056, 1089 (rounded display). They agree with half-integer squares, e.g. 27² = 729, 27.5² = 756.25, 28² = 784. Exact unrounded original levels and base-selection rule remain unknown.

TRENDIKATERR inputs: action box enabled, BUY / SELL enabled, Ribbon EMA length 11. Style exposes BRAHMASTRA Ribbon with Bearish, Recovery, Bullish colors; Buy below bar; Sell above bar; Holding BUY / Holding SELL background colors. Last ribbon values: call 757.56, put 849.18. Both action boxes say bearish. Call: BUYERS ABSENT; EXIT CE → BUY PE. Put: SELLERS ARE BACK; BUY CE. The exact conditions behind these differing messages are unknown.

## Next validation

1. Export original OHLC and ribbon values for at least 100–200 completed bars with timestamps. Compare close, open, hl2, and hlc3 EMA(11) candidates after a warm-up period.
2. Record original BUY/SELL timestamps and ribbon colors over several sessions. Test state hypotheses on separate sessions; report mismatches and coverage, not just selected matches.
3. Read exact TTC bases across session boundaries, compare with daily open and previous close, and test its handling of decimals and gaps.
4. Compare live signal appearance before/after bar close and reload to detect repainting. A static screenshot cannot establish this.

Pine references consulted: [EMA declarations](https://www.tradingview.com/pine-script-docs/language/declaration-statements/), [repainting](https://www.tradingview.com/pine-script-docs/concepts/repainting/).
