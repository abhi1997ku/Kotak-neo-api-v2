from dataclasses import replace
from datetime import date, timedelta
import copy

import pytest

from screener.stock_paper import new_account, equity
from screener.swing_data import BENCHMARK, merge_bars
from screener.swing_engine import Config, VERSION, costs, evaluate, prepare, process_day, scan_day, size, open_risk


def feature(day="2026-10-12", **kwargs):
    return dict(dict(date=day, open=100, high=101, low=98, close=100, volume=2_000_000,
        vol20=1_000_000, turnover=200_000_000, sma50=95, sma200=90, sma50_old=93,
        ema20=99, ema20_prev=98, prev_low=98, prev_high=99, prev_close=98.5,
        atr=1, atr_prior=1, low5=97, high20=99, high55=99, high250=120,
        contraction_atr=.7, contraction_range=.5, volatility=.15, relative_strength=12,
        split=0, dividend=0), **kwargs)


def candidate(symbol="ABC", sector="Tech", cfg=Config()):
    return evaluate(dict(symbol=symbol, industry=sector), feature(), "BULLISH", cfg, "breakout20")


def queued_account(cfg=Config()):
    account = new_account()
    c = candidate(cfg=cfg)
    assert c["status"] == "BUY CANDIDATE"
    account["orders"] = [dict(c, id="ABC:2026-10-12", created_date="2026-10-12", status="QUEUED")]
    account["last_session"] = "2026-10-12"
    return account


def run(account, day, cfg=Config(), **changes):
    process_day(account, {BENCHMARK: feature(day), "ABC": feature(day, **changes)}, day, {}, cfg)


def test_independent_patterns_and_mandatory_controls():
    for strategy in ("breakout20", "breakout55", "pullback", "contraction"):
        row = evaluate(dict(symbol="ABC"), feature(), "BULLISH", Config(), strategy)
        assert row["status"] == "BUY CANDIDATE"
        assert row["score"] == pytest.approx(sum(row["score_components"].values()), abs=.01)
        assert 0 <= row["score"] <= 100
    weak = feature(contraction_atr=1.1)
    assert evaluate(dict(symbol="ABC"), weak, "BULLISH", Config(), "contraction")["status"] == "WATCH"
    assert evaluate(dict(symbol="ABC"), feature(), "BEARISH", Config(min_score=0), "breakout20")["status"] == "WATCH"
    assert evaluate(dict(symbol="ABC"), feature(turnover=100), "BULLISH", Config(min_score=0), "breakout20")["status"] == "WATCH"


def test_next_open_not_signal_close_and_no_fixed_profit_exit():
    account = queued_account()
    run(account, "2026-10-12")
    assert not account["trades"]
    run(account, "2026-10-13", high=140, close=139, low=99)
    t = account["trades"][0]
    assert t["entry"] == pytest.approx(100.05)
    assert t["status"] == "OPEN"
    assert t["target"] is None
    assert t["planned_risk"] <= 500
    assert t["stop"] > t["initial_stop"]
    assert equity(account) == pytest.approx(account["cash"]+t["quantity"]*139)


def test_trail_effective_next_session_gap_execution_and_reconciliation():
    account = queued_account()
    run(account, "2026-10-13", high=110, low=98, close=109)
    t = account["trades"][0]
    assert t["status"] == "OPEN"  # today's low is below newly raised stop
    assert t["stop"] == 107
    run(account, "2026-10-14", open=103, high=105, low=102, close=104)
    assert t["status"] == "EXIT"
    assert t["exit_reason"] == "GAP_STOP"
    assert t["exit"] == pytest.approx(103*.9995)
    assert equity(account) == pytest.approx(100000+t["net_pnl"])
    assert any(e["kind"] == "STOP_TRIGGER" for e in account["events"])
    assert any(e["kind"] == "EXIT_EXECUTED" for e in account["events"])


def test_stop_never_widens_as_atr_increases():
    account = queued_account()
    run(account, "2026-10-13", high=105, low=98, close=104)
    t = account["trades"][0]
    stop = t["stop"]
    run(account, "2026-10-14", open=104, high=110, low=103, close=109, atr=5)
    assert t["stop"] == stop


def test_ema_reversal_signal_exits_next_open():
    cfg = Config(exit_style="ema")
    account = queued_account(cfg)
    run(account, "2026-10-13", cfg, ema20=101, ema20_prev=101, prev_close=100)
    t = account["trades"][0]
    assert t["status"] == "OPEN" and t["pending_exit"]
    run(account, "2026-10-14", cfg, open=99)
    assert t["exit_reason"] == "TREND_REVERSAL"
    assert t["exit"] == pytest.approx(99*.9995)


def test_missing_active_bar_blocks_replay_and_duplicate_replay_is_noop():
    account = queued_account()
    process_day(account, {BENCHMARK: feature()}, "2026-10-13", {}, Config())
    assert account["warnings"] and not account["trades"]
    account["warnings"] = []
    run(account, "2026-10-13")
    before = copy.deepcopy(account)
    run(account, "2026-10-13")
    assert account == before


def test_split_changes_units_not_cash_profit_and_dividend_credits():
    account = queued_account()
    run(account, "2026-10-13")
    t = account["trades"][0]
    qty, stop = t["quantity"], t["stop"]
    run(account, "2026-10-14", open=50, high=50.5, low=49.5, close=50,
        atr=.5, low5=48.5, split=2, dividend=1)
    assert t["quantity"] == qty*2
    assert t["entry"] == pytest.approx(50.025)
    assert t["stop"] >= stop/2
    assert t["dividends"] == qty*2


def test_portfolio_risk_correlation_duplicate_and_cash_limits():
    account = queued_account()
    run(account, "2026-10-13")
    cfg = Config()
    q, risk, reason = size(account, candidate("ABC"), 100, cfg, {}, "2026-10-14")
    assert q == 0 and reason == "Duplicate position"
    account["cash"] = 1
    assert size(account, candidate("XYZ"), 100, cfg, {}, "2026-10-14")[0] == 0
    account["cash"] = 90000
    strict = replace(cfg, portfolio_risk_pct=.0001)
    assert size(account, candidate("XYZ"), 100, strict, {}, "2026-10-14")[0] == 0
    correlated = replace(cfg, correlated_cap=.01)
    assert size(account, candidate("XYZ"), 100, correlated, {}, "2026-10-14")[0] == 0


def test_scan_reserves_combined_risk_and_deduplicates_strategies():
    day = "2026-10-12"
    prepared = dict(members=[dict(symbol=s, industry=s) for s in ("A", "B", "C", "D", "E")],
                    snapshots={day: {s: feature() for s in (BENCHMARK, "A", "B", "C", "D", "E")}}, returns={})
    account = new_account()
    rows = scan_day(account, prepared, day, Config())
    orders = account["orders"]
    assert len(orders) <= 4
    assert len({o["symbol"] for o in orders}) == len(orders)
    assert sum(o["estimated_risk"] for o in orders) <= 2000
    assert sum(o["capital_required"] for o in orders) <= 35000  # unknown correlation conservative


def test_cost_components_and_cache_corporate_action_refresh():
    buy, sell = costs(10000, "buy"), costs(10000, "sell")
    assert buy["stt"] == sell["stt"] == 10
    assert buy["stamp"] == pytest.approx(1.5) and sell["stamp"] == 0
    assert sell["depository"] == 20
    assert buy["total"] == pytest.approx(sum(v for k, v in buy.items() if k != "total"))
    assert merge_bars([dict(date="2026-10-09", close=100)], [dict(date="2026-10-09", close=50, split=2)]) is None


def test_indicators_exclude_current_high_and_do_not_see_future():
    bars = []
    for i in range(300):
        p = 100+i/10
        bars.append(dict(date=(date(2025, 1, 1)+timedelta(days=i)).isoformat(), open=p, high=p+1, low=p-1,
                         close=p, volume=2_000_000, split=0, dividend=0))
    bars[-1]["high"] = 999
    data = dict(frames={BENCHMARK: bars, "ABC": bars}, members=[dict(symbol="ABC")])
    old = prepare(data)
    last = old["days"][-1]
    assert old["snapshots"][last]["ABC"]["high20"] < 200
    future = dict(bars[-1], date="2027-01-01", close=1000, high=1100)
    fresh = prepare(dict(frames={BENCHMARK: bars+[future], "ABC": bars+[future]}, members=data["members"]))
    assert old["snapshots"][last] == fresh["snapshots"][last]
