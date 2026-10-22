import copy

import pytest

from screener.stock_paper import advance, equity
from screener.stock_swing_experiments import alternative, research_exit, objective
from test_stock_paper import account_with_orders, bar, histories


def test_partial_exit_then_trailing_gap_reconciles_account():
    account = account_with_orders()
    handler = research_exit("partial5_trail_30")
    advance(account, {"ABC": [bar("2026-10-12", high=106, low=99, close=105)]}, ["2026-10-12"], exit_handler=handler)
    trade = account["trades"][0]
    assert trade["status"] == "OPEN"  # Same-day low cannot hit a newly raised stop.
    assert trade["partial_taken"]
    assert trade["stop"] == pytest.approx(105 * 0.97)
    assert equity(account) == pytest.approx(100_000 + trade["quantity"] * 105 + trade["partial_proceeds"] - trade["cost"])
    advance(account, {"ABC": [bar("2026-10-13", opened=99, high=101, low=98, close=100)]}, ["2026-10-13"], exit_handler=handler)
    assert trade["status"] == "STOP"
    assert trade["exit"] == pytest.approx(99 * 0.9995)
    assert equity(account) == pytest.approx(100_000 + trade["net_pnl"])
    assert len(trade["exit_legs"]) == 2


def test_stop_before_partial_and_target_on_ambiguous_bar():
    account = account_with_orders()
    advance(account, {"ABC": [bar("2026-10-12", high=120, low=94)]}, ["2026-10-12"], exit_handler=research_exit("partial5_trail_30"))
    trade = account["trades"][0]
    assert trade["status"] == "STOP"
    assert not trade.get("partial_taken")
    assert len(trade["exit_legs"]) == 1


def test_partial_and_final_target_same_day_counts_all_proceeds():
    account = account_with_orders()
    advance(account, {"ABC": [bar("2026-10-12", high=116, low=99)]}, ["2026-10-12"], exit_handler=research_exit("partial5_trail_30"))
    trade = account["trades"][0]
    assert trade["status"] == "TARGET"
    assert len(trade["exit_legs"]) == 2
    assert equity(account) == pytest.approx(100_000 + trade["net_pnl"])


def test_split_after_partial_adjusts_remaining_shares_and_trail():
    account = account_with_orders()
    handler = research_exit("partial5_trail_30")
    advance(account, {"ABC": [bar("2026-10-12", high=106, low=99, close=105)]}, ["2026-10-12"], exit_handler=handler)
    before = copy.deepcopy(account["trades"][0])
    advance(account, {"ABC": [bar("2026-10-13", opened=52.5, high=53, low=52, close=52.5, split=2, dividend=1)]}, ["2026-10-13"], exit_handler=handler)
    trade = account["trades"][0]
    assert trade["status"] == "OPEN"
    assert trade["quantity"] == before["quantity"] * 2
    assert trade["trail_peak"] == pytest.approx(before["trail_peak"] / 2)
    assert trade["partial_proceeds"] == before["partial_proceeds"]
    assert trade["dividends"] == trade["quantity"]


@pytest.mark.parametrize("style,limit", [("target8_20", 20), ("partial5_trail_30", 30)])
def test_longer_holding_period_does_not_keep_original_ten_session_exit(style, limit):
    from datetime import date, timedelta
    account = account_with_orders()
    days = [(date(2026, 10, 12) + timedelta(days=i)).isoformat() for i in range(limit)]
    advance(account, {"ABC": [bar(day) for day in days]}, days, exit_handler=research_exit(style))
    trade = account["trades"][0]
    assert trade["status"] == "TIME_EXIT"
    assert trade["sessions"] == limit
    assert equity(account) == pytest.approx(100_000 + trade["net_pnl"])


def test_pullback_requires_confirmation_and_aligned_data():
    stock, index = histories()
    member = dict(symbol="ABC", industry="Tech")
    assert alternative(member, stock, index, "pullback") is not None
    assert alternative(member, stock[:-1], index, "pullback") is None
    stock[-1]["close"] = stock[-2]["close"]
    assert alternative(member, stock, index, "pullback") is None


def test_retest_requires_an_earlier_breakout():
    stock, index = histories()
    member = dict(symbol="ABC", industry="Tech")
    assert alternative(member, stock, index, "retest") is None
    stock[-6].update(open=109, high=111, close=110.5)
    assert alternative(member, stock, index, "retest") is not None


def test_selection_rejects_tiny_trade_samples():
    assert objective(dict(return_pct=20, max_drawdown_pct=1, closed_trades=29)) == -1e9
