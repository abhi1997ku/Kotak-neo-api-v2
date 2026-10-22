from datetime import datetime, timedelta, date

import pytest

from screener.stock_paper import IST, PaperStore, advance, equity, evaluate_stock, new_account


def bar(day, opened=100, high=102, low=98, close=101, volume=2_000_000, **extra):
    return dict(date=day, open=opened, high=high, low=low, close=close, volume=volume, **extra)


def order(symbol="ABC", created="2026-10-09", sector=None, score=90):
    return dict(id=f"{symbol}:{created}", symbol=symbol, sector=sector or symbol,
                status="QUEUED", created_date=created, score=score,
                reference=100, stop=95, target=115, max_entry=102.5)


def account_with_orders(*orders):
    account = new_account()
    account["orders"] = list(orders or [order()])
    account["last_session"] = "2026-10-09"
    return account


def test_creation_day_is_never_a_fill():
    account = account_with_orders(order(created="2026-10-12"))
    advance(account, {"ABC": [bar("2026-10-12")]}, ["2026-10-12"])
    assert account["trades"] == []
    assert account["cash"] == 100_000
    assert account["orders"][0]["status"] == "QUEUED"


def test_next_open_fill_cost_risk_cash_and_idempotence():
    account = account_with_orders()
    frames = {"ABC": [bar("2026-10-12")]}
    advance(account, frames, ["2026-10-12"])
    trade = account["trades"][0]
    assert trade["entry"] == pytest.approx(100.05)
    assert trade["planned_risk"] <= 750
    assert trade["cost"] <= 25_000
    assert account["cash"] + trade["cost"] == pytest.approx(100_000)
    before = repr(account)
    advance(account, frames, ["2026-10-12"])
    assert repr(account) == before


@pytest.mark.parametrize("opened", [90, 99-0.01, 110])
def test_opening_gaps_skip_instead_of_fabricating_reference_fill(opened):
    account = account_with_orders()
    advance(account, {"ABC": [bar("2026-10-12", opened=opened, high=116, low=85)]}, ["2026-10-12"])
    assert not account["trades"]
    assert account["orders"][0]["status"] == "SKIPPED"
    assert account["cash"] == 100_000


def test_stop_first_when_both_levels_touched_after_open_entry():
    account = account_with_orders()
    advance(account, {"ABC": [bar("2026-10-12", high=116, low=94)]}, ["2026-10-12"])
    trade = account["trades"][0]
    assert trade["status"] == "STOP"
    assert trade["exit"] == pytest.approx(95 * 0.9995)
    assert equity(account) == pytest.approx(100_000 + trade["net_pnl"])


def test_existing_position_gap_stop_fills_at_worse_open():
    account = account_with_orders()
    advance(account, {"ABC": [bar("2026-10-12"), bar("2026-10-13", opened=90, high=94, low=88, close=92)]}, ["2026-10-12", "2026-10-13"])
    assert account["trades"][0]["exit"] == pytest.approx(90 * 0.9995)


def test_weekly_limit_counts_fills_across_scans_and_resets_monday():
    account = account_with_orders()
    for day, count in [("2026-10-12", 4), ("2026-10-13", 2), ("2026-10-14", 0), ("2026-10-19", 4)]:
        before = len(account["trades"])
        account["orders"] += [order(f"S{day}{i}", created=(date.fromisoformat(day)-timedelta(days=1)).isoformat()) for i in range(4)]
        frames = {o["symbol"]: [bar(day, high=116)] for o in account["orders"] if o["status"] == "QUEUED"}
        advance(account, frames, [day])
        assert len(account["trades"]) - before == count
        assert account["cash"] >= 0


def test_four_position_cap_and_sector_cap():
    account = account_with_orders(*[order(f"S{i}") for i in range(8)])
    advance(account, {o["symbol"]: [bar("2026-10-12")] for o in account["orders"]}, ["2026-10-12"])
    assert len(account["trades"]) == 4
    account = account_with_orders(*[order(f"B{i}", sector="Banks") for i in range(3)])
    advance(account, {o["symbol"]: [bar("2026-10-12")] for o in account["orders"]}, ["2026-10-12"])
    assert len(account["trades"]) == 2


def test_missing_active_bar_blocks_all_replay_and_preserves_cash():
    account = account_with_orders(order("A"), order("B"))
    advance(account, {"A": [bar("2026-10-12")]}, ["2026-10-12"])
    assert account["last_session"] == "2026-10-09"
    assert not account["trades"]
    assert account["warnings"]


def test_same_day_exit_cash_cannot_fund_an_opening_entry():
    account = account_with_orders()
    account["cash"] = 100
    advance(account, {"ABC": [bar("2026-10-12", high=116)]}, ["2026-10-12"])
    assert not account["trades"]


def test_ten_session_exit_and_persistent_month_accounting(tmp_path):
    account = account_with_orders()
    days = [(date(2026, 10, 12) + timedelta(days=i)).isoformat() for i in range(10)]
    advance(account, {"ABC": [bar(day) for day in days]}, days)
    assert account["trades"][0]["status"] == "TIME_EXIT"
    assert account["trades"][0]["exit_date"] == days[-1]
    summary = PaperStore.summary(account, datetime(2026, 10, 22, tzinfo=IST))
    assert summary["month_pnl"] == pytest.approx(summary["realized_pnl"])
    november = PaperStore.summary(account, datetime(2026, 11, 1, tzinfo=IST))
    assert november["month_pnl"] == pytest.approx(0)
    assert november["monthly_target"] == pytest.approx(equity(account) * 0.05)


def test_splits_and_dividend_do_not_fabricate_a_loss():
    account = account_with_orders()
    advance(account, {"ABC": [bar("2026-10-12")]}, ["2026-10-12"])
    quantity = account["trades"][0]["quantity"]
    advance(account, {"ABC": [bar("2026-10-13", opened=50, high=51, low=49, close=50.5, split=2, dividend=1)]}, ["2026-10-13"])
    trade = account["trades"][0]
    assert trade["status"] == "OPEN"
    assert trade["quantity"] == quantity * 2
    assert trade["entry"] == pytest.approx(50.025)
    assert trade["dividends"] == quantity * 2


def histories():
    days = [(date(2025, 12, 14) + timedelta(days=i)).isoformat() for i in range(300)]
    stock, index = [], []
    for i, day in enumerate(days):
        price = 80 + i * 0.1
        stock.append(bar(day, opened=price, high=price+0.5, low=price-2, close=price))
        index.append(bar(day, opened=100+i*0.01, high=105+i*0.01, low=99+i*0.01, close=100+i*0.01))
    stock[-1] = bar(days[-1], opened=110, high=111.5, low=109, close=111.2, volume=4_000_000)
    return stock, index


def test_qualified_selection_rejects_weak_volume_and_stale_stock():
    stock, index = histories()
    result = evaluate_stock("ABC", "A", "Tech", stock, index)
    assert result["status"] == "QUALIFIED"
    stock[-1]["volume"] = 100
    assert evaluate_stock("ABC", "A", "Tech", stock, index)["status"] == "WATCH"
    assert evaluate_stock("ABC", "A", "Tech", stock[:-1], index)["status"] == "REJECTED"


def test_scan_restart_deduplication_history_and_no_backdated_entries(tmp_path):
    stock, index = histories()
    now = datetime.fromisoformat(index[-1]["date"] + "T17:00:00").replace(tzinfo=IST)
    path = tmp_path / "paper.sqlite3"
    store = PaperStore(path)
    members = [{"symbol": "ABC", "name": "A", "industry": "Tech"}]
    frames = {"ABC": stock, "^NSEI": index}
    first = store.scan(frames, members, now, "Test fixture")
    assert len(first["orders"]) == 1
    assert first["trades"] == []
    second = PaperStore(path).scan(frames, members, now, "Test fixture")
    assert len(second["orders"]) == 1
    assert len(store.history()) == 2
    assert store.history(1)["stocks"][0]["status"] == "QUEUED"
    assert second["weekly_entries"] == 0


def test_bad_benchmark_does_not_mutate_account(tmp_path):
    store = PaperStore(tmp_path / "paper.sqlite3")
    with pytest.raises(ValueError, match="Fresh completed"):
        store.scan({}, [], datetime(2026, 10, 12, tzinfo=IST), "Unavailable")
    assert store.read() == new_account()


def test_daily_provider_excludes_incomplete_and_invalid_bars():
    import pandas as pd
    from backend.stock_paper_service import completed_bars
    frame = pd.DataFrame([dict(Open=100, High=103, Low=99, Close=102, Volume=100),
                          dict(Open=100, High=103, Low=99, Close=102, Volume=100)],
                         index=pd.to_datetime(["2026-10-09", "2026-10-12"]))
    assert len(completed_bars(frame, datetime(2026, 10, 12, 12, tzinfo=IST))) == 1
    assert len(completed_bars(frame, datetime(2026, 10, 12, 17, tzinfo=IST))) == 2
    frame.loc[frame.index[-1], "High"] = 90
    assert len(completed_bars(frame, datetime(2026, 10, 12, 17, tzinfo=IST))) == 1


def test_routes_do_not_call_broker_and_retire_old_scanner(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from backend.routes.screener import router, service, portfolio, scan_history, export
    monkeypatch.setattr(service, "store", PaperStore(tmp_path / "route.sqlite3"))
    assert portfolio()["cash"] == 100_000
    assert next(r for r in router.routes if r.path == "/screener/scan").status_code == 410
    with pytest.raises(HTTPException) as missing:
        scan_history(999)
    assert missing.value.status_code == 404
    assert "attachment" in export().headers["content-disposition"]
