"""Validate cached research prices and produce a readable coverage-aware report."""
import json
import math
from pathlib import Path

import pandas as pd

from screener.order_block_year_options import historical_lot_size, summary

ROOT = Path('backend/data/one_year_research')


def main():
    report = json.loads((ROOT / 'options_report.json').read_text())
    source = json.loads((ROOT / 'report.json').read_text())
    rows = report['trades']
    assert len(rows) == len(source['trades'])
    assert len({(r['fill_at'], r['side']) for r in rows}) == len(rows)
    checked = 0
    for path in (ROOT / 'options').glob('*_options_*.json'):
        for payload in json.loads(path.read_text())['data'].values():
            bars = (payload or {}).get('candles') or []
            assert len({b['ts'] for b in bars}) == len(bars), str(path)
            for b in bars:
                assert all(math.isfinite(float(b[k])) for k in ('o', 'h', 'l', 'c', 'v'))
                assert b['l'] <= min(b['o'], b['c']) <= max(b['o'], b['c']) <= b['h']
                assert b['v'] >= 0
                checked += 1
    for r in rows:
        if 'quantity' in r:
            assert r['quantity'] == 2 * historical_lot_size(r['expiry'])
            assert r['expiry'] >= r['session']
            assert pd.Timestamp(r['spot_reference_at']) == pd.Timestamp(r['fill_at']) - pd.Timedelta(minutes=1)
        if r['coverage'] == 'PRICED':
            assert pd.Timestamp(r['option_entry_at']) < pd.Timestamp(r['option_exit_at'])
            assert r['gross_option_pnl'] == round((r['option_exit'] - r['option_entry']) * r['quantity'], 2)
            assert abs(r['scenario_net_pnl'] - (r['gross_option_pnl'] - 2 * r['quantity'] - 40)) < .011
    audit = dict(trades_accounted_for=len(rows), option_candles_checked=checked,
                 unresolved_underlying_outcomes={}, full_execution_backtest=False)
    for r in rows:
        if r['coverage'] != 'PRICED':
            key = r['result']
            audit['unresolved_underlying_outcomes'][key] = audit['unresolved_underlying_outcomes'].get(key, 0) + 1
    # Refresh old running-process assumption wording without changing numerical results.
    report['assumptions'][0] = 'Frozen futures signals; nearest monthly expiry; two listed strikes ITM from previous completed spot minute; two historical NSE lots per FAOP70616'
    report['audit'] = audit
    (ROOT / 'options_report.json').write_text(json.dumps(report, indent=2))
    lines = ['# One-year BANKNIFTY option-price research', '',
             'Window: 1 November 2025 through 31 October 2026. Paper research only.', '',
             '**This is candle-based option-price attribution, not a complete executable options-P&L backtest.** Missing fills and same-minute round trips remain unresolved. Excluding unresolved stop-outs can bias the priced subset upwards.', '',
             '| Model | Priced trades | Wins | P&L (INR) | Profit factor | Closed-trade drawdown (INR) |',
             '|---|---:|---:|---:|---:|---:|']
    for label, key in [('Next-minute open, gross', 'summary_gross'), ('Hypothetical costs', 'scenario_summary'), ('Adverse event-bar OHLC stress', 'adverse_ohlc_summary')]:
        s = report[key]
        lines.append(f"| {label} | {s['trades']} | {s['wins']} | {s['pnl']:,.2f} | {s['profit_factor']} | {s['max_closed_trade_drawdown']:,.2f} |")
    lines += ['', '## Coverage', '', *[f'- {k}: {v}' for k, v in report['coverage'].items()], '',
              '## Monthly priced-subset gross results', '', '| Month | Priced | P&L (INR) |', '|---|---:|---:|']
    for month, s in report['monthly'].items():
        lines.append(f"| {month} | {s['trades']} | {s['pnl']:,.2f} |")
    lines += ['', '## Assumptions and limits', '', *['- ' + x for x in report['assumptions']],
              '- Same-minute outcomes and unpriced trades are not zero-profit trades.',
              '- Signals were selected using previously inspected data; this is not an independent holdout.',
              '- Underlying calendar coverage exceptions still need holiday reconciliation.',
              '- Adverse OHLC stress uses different event-bar prices, not a bound on next-minute execution.', '',
              'Historical lot schedule: [NSE FAOP70616](https://nsearchives.nseindia.com/content/circulars/FAOP70616.pdf).', '',
              'Detailed data: [options_report.json](options_report.json).']
    (ROOT / 'OPTIONS_RESEARCH.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
