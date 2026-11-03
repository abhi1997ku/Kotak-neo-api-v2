import pytest

from screener.order_block_year_options import historical_lot_size, select, summary


def test_historical_quantity_changes_with_expiry():
    assert historical_lot_size('2025-11-25') == 35
    assert historical_lot_size('2025-12-30') == 35
    assert historical_lot_size('2026-01-27') == 30
    with pytest.raises(ValueError):
        historical_lot_size('2027-01-26')


def test_two_itm_strikes_and_tie():
    rows = [dict(option_type=kind, strike_price=strike)
            for kind in ('CE', 'PE') for strike in range(500, 1200, 100)]
    assert select(rows, 850, 'long')['strike_price'] == 600
    assert select(rows, 850, 'short')['strike_price'] == 1000
    with pytest.raises(ValueError):
        select(rows, 500, 'long')


def test_summary_excludes_unknown_not_as_zero_and_counts_drawdown():
    result = summary([{'pnl': x} for x in (100, -60, None, -70, 50)], 'pnl')
    assert result['trades'] == 4
    assert result['pnl'] == 20
    assert result['max_closed_trade_drawdown'] == 130
    assert result['wins'] == 2
