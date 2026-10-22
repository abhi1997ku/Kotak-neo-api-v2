import copy

import pytest

from screener.stock_paper_backtest import HistoricalView, statistics


def sample(day, price=50, split=0):
    return dict(date=day, open=price, high=price+1, low=price-1, close=price,
                volume=200, dividend=0, split=split)


def test_historical_view_excludes_future_prices_and_restores_split_units():
    frames = {"ABC": [sample("2025-01-01"), sample("2025-01-02", split=2), sample("2025-01-03")]}
    before = HistoricalView(frames).at("2025-01-01")["ABC"]
    assert len(before) == 1
    assert before[0]["close"] == 100
    assert before[0]["volume"] == 100
    on_split = HistoricalView(frames).at("2025-01-02")["ABC"]
    assert len(on_split) == 2
    assert on_split[0]["close"] == 50
    assert on_split[1]["split"] == 2
    changed = copy.deepcopy(frames)
    changed["ABC"][-1]["close"] = 9000
    assert HistoricalView(changed).at("2025-01-01")["ABC"] == before
    assert frames["ABC"][0]["close"] == 50  # Cache is not mutated.


def test_multiple_splits_use_only_after_decision_date_for_unit_conversion():
    frames = {"ABC": [sample("2025-01-01"), sample("2025-01-02", split=2), sample("2025-01-03", split=3)]}
    view = HistoricalView(frames)
    assert view.at("2025-01-01")["ABC"][-1]["close"] == 300
    assert view.at("2025-01-02")["ABC"][-1]["close"] == 150
    assert view.at("2025-01-03")["ABC"][-1]["close"] == 50


def test_statistics_excludes_boundary_months_and_measures_drawdown():
    account = {"trades": [], "orders": [], "equity_history": [
        {"date": "2025-01-15", "equity": 100000},
        {"date": "2025-01-31", "equity": 110000},
        {"date": "2025-02-28", "equity": 99000},
        {"date": "2025-03-31", "equity": 105000},
        {"date": "2025-04-10", "equity": 106000},
    ]}
    result = statistics(account, "2025-01-15", "2025-04-10", [sample("2025-01-15"), sample("2025-04-10", price=55)])
    assert result["return_pct"] == pytest.approx(6)
    assert result["max_drawdown_pct"] == pytest.approx(10)
    assert result["months_at_least_5pct"] == 1
    assert result["complete_months"] == 2
    assert result["win_rate_pct"] is None
    assert result["nifty50_price_return_pct"] == pytest.approx(10)
