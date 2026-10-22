from datetime import date, datetime, timedelta

import pytest

from screener.stock_paper import IST, PaperStore, advance, evaluate_stock
from screener.stock_selective import design_candidate, policy, wilson_lower
from screener.stock_selective_research import nonoverlapping, split_records, estimate
from test_stock_paper import account_with_orders, bar, histories, order


def selective_order():
    candidate = dict(order(), status="QUALIFIED", as_of="2026-10-09", stop=96)
    candidate = design_candidate(candidate)
    candidate["status"] = "QUEUED"  # Isolated execution test, not the live gate.
    return candidate


def test_unvalidated_high_rank_is_watch_only_and_no_probability_is_invented():
    candidate = dict(order(), status="QUALIFIED", as_of="2026-10-09", stop=96, score=100)
    result = design_candidate(candidate)
    assert result["status"] == "WATCH"
    assert result["probability"] is None
    assert result["target"] == 120
    assert not policy()["entries_enabled"]
    assert policy()["monthly_goal_pct"] is None
    candidate["stop"] = 94
    assert design_candidate(candidate)["status"] == "REJECTED"


def test_selective_scan_cancels_old_pending_ideas_without_erasing_history(tmp_path):
    stock, index = histories()
    now = datetime.fromisoformat(index[-1]["date"] + "T17:00:00").replace(tzinfo=IST)
    path = tmp_path / "paper.sqlite3"
    legacy = PaperStore(path)
    frames, members = {"ABC": stock, "^NSEI": index}, [dict(symbol="ABC", name="A", industry="Tech")]
    legacy.scan(frames, members, now, "Fixture")
    assert legacy.read()["orders"][0]["status"] == "QUEUED"
    selective = PaperStore(path, selective=True)
    selective.scan(frames, members, now, "Fixture")
    account = selective.read()
    assert account["orders"][0]["status"] == "CANCELLED"
    assert not account["trades"]
    assert len(selective.history()) == 2
    assert account["latest_scan"]["stocks"][0]["status"] == "WATCH"
    assert selective.summary(account, now)["monthly_target"] is None


def test_actual_fill_defines_target_and_five_percent_stop_cap():
    account = account_with_orders(selective_order())
    advance(account, {"ABC": [bar("2026-10-12", opened=101.1)]}, ["2026-10-12"])
    assert account["orders"][0]["status"] == "SKIPPED"  # 101.15055 to 96 exceeds 5%.
    account = account_with_orders(selective_order())
    advance(account, {"ABC": [bar("2026-10-12")]}, ["2026-10-12"])
    trade = account["trades"][0]
    assert trade["target"] == pytest.approx(trade["entry"] * 1.2)
    assert trade["planned_risk"] <= 750


def test_sixty_session_horizon_and_target_before_stop_semantics():
    account = account_with_orders(selective_order())
    days = [(date(2026, 10, 12) + timedelta(days=i)).isoformat() for i in range(60)]
    advance(account, {"ABC": [bar(day) for day in days]}, days)
    assert account["trades"][0]["status"] == "TIME_EXIT"
    assert account["trades"][0]["sessions"] == 60
    account = account_with_orders(selective_order())
    advance(account, {"ABC": [bar(days[0], high=125, low=95)]}, days[:1])
    assert account["trades"][0]["status"] == "STOP"


def test_probability_samples_exclude_overlaps_and_purge_label_windows():
    rows = [dict(day="2024-01-01", label_end="2024-03-01", symbol="A", y=1),
            dict(day="2024-02-01", label_end="2024-04-01", symbol="A", y=1),
            dict(day="2024-03-02", label_end="2024-05-01", symbol="A", y=0),
            dict(day="2024-05-02", label_end="2024-07-01", symbol="A", y=1)]
    assert len(nonoverlapping(rows)) == 3
    train, calibration, test = split_records(rows, "2024-03-02", "2024-05-02")
    assert [r["day"] for r in train] == ["2024-01-01"]
    assert [r["day"] for r in calibration] == ["2024-03-02"]
    assert [r["day"] for r in test] == ["2024-05-02"]
    assert estimate(rows, "2024-03-02", "2024-05-02")["status"] == "INSUFFICIENT_DATA"
    assert wilson_lower(8, 10) < 0.8
    assert wilson_lower(95, 100) > 0.8
