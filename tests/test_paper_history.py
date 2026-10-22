from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.paper_history import parse_history
from backend.paper_trading import BankNiftySignalEngine, OneMinuteCandle

INDIA = ZoneInfo("Asia/Kolkata")


def minute(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=INDIA).timestamp() // 60)


@pytest.mark.parametrize("step,direction", [(10, "long"), (-10, "short")])
def test_prior_session_seed_allows_entry_after_three_fresh_candles(step, direction):
    start = minute("2026-10-09T15:00:00")
    prior = [OneMinuteCandle(start+i, 55000, 55000, 55000, 55000, 1000000) for i in range(25)]
    engine = BankNiftySignalEngine()
    engine.session_day = "2026-10-12"
    engine.load_completed_candles(prior, allow_initial_signal=True)
    assert engine.diagnostics()["phase"] == "waiting_session_confirmation"
    opening = minute("2026-10-12T09:15:00")
    # Zero starting volume is valid and must not lose the first volume delta.
    for i in range(3):
        assert engine.on_tick(55000+step*(i+1), i*100, (opening+i)*60) is None
    assert engine.on_tick(55000+step*4, 300, (opening+3)*60) == direction
    assert engine.on_tick(55000+step*5, 400, (opening+4)*60) is None
    assert len(engine._session_candles()) == 4


def test_previous_volume_cannot_change_today_regime():
    results = []
    for volume in (1, 100000000):
        engine = BankNiftySignalEngine()
        engine.session_day = "2026-10-12"
        old = minute("2026-10-09T15:00:00")
        new = minute("2026-10-12T09:15:00")
        candles = [OneMinuteCandle(old+i, 55000, 56000, 54000, 55000, volume) for i in range(25)]
        candles += [OneMinuteCandle(new+i, 55010+i*10, 55010+i*10, 55010+i*10, 55010+i*10, 100) for i in range(3)]
        engine.load_completed_candles(candles)
        results.append(engine._market_regime())
    assert results == ["bullish", "bullish"]


def test_history_filters_unclosed_candles_and_rejects_bad_ohlcv():
    now = datetime(2026, 10, 12, 9, 17, 30, tzinfo=INDIA)
    rows = [[f"2026-10-12T09:{i}:00+05:30", 100, 102, 99, 101, 10] for i in (15, 16, 17)]
    payload = {"status": "success", "interval": "1min", "data": {"candles": rows}}
    assert len(parse_history(payload, now)) == 2
    rows[0][2] = 98
    with pytest.raises(ValueError):
        parse_history(payload, now)


def test_seeded_candles_ignore_overlapping_and_out_of_order_ticks():
    engine = BankNiftySignalEngine()
    engine.load_completed_candles([OneMinuteCandle(100, 10, 10, 10, 10, 100)])
    assert engine.on_tick(99, 100, 100*60) is None
    assert engine._current is None
    engine.on_tick(11, 110, 101*60)
    engine.on_tick(99, 120, 100*60)
    assert engine._current.close == 11


def test_contract_cache_survives_day_reset_without_mixing_contracts(tmp_path):
    from backend.paper_journal import PaperJournal
    journal = PaperJournal(tmp_path / "paper.db")
    bar = OneMinuteCandle(minute("2026-10-09T15:00:00"), 100, 101, 99, 100, 10)
    journal.cache_candles("october", [bar])
    journal.begin_day("2026-10-12")
    now = datetime(2026, 10, 12, 9, 15, tzinfo=INDIA)
    assert journal.cached_candles("october", now) == [bar]
    assert journal.cached_candles("november", now) == []


@pytest.mark.parametrize("has_cache", [True, False])
def test_history_failure_uses_only_matching_contract_cache(tmp_path, monkeypatch, has_cache):
    import asyncio
    from backend import paper_agent
    from backend.paper_journal import PaperJournal
    agent = paper_agent.PaperAgent.__new__(paper_agent.PaperAgent)
    agent.journal = PaperJournal(tmp_path / "paper.db")
    agent.engine = BankNiftySignalEngine()
    agent.trade_day = "2026-10-12"
    agent.trade = None
    agent.state = {"trades_today": 0}
    monkeypatch.setattr(agent, "_india_now", lambda: datetime(2026, 10, 12, 9, 15, tzinfo=INDIA))
    def unavailable(*args):
        raise ValueError("unavailable")
    monkeypatch.setattr(paper_agent, "fetch_history", unavailable)
    prior = [OneMinuteCandle(minute("2026-10-09T15:00:00")+i, 100, 100, 100, 100, 10) for i in range(25)]
    agent.journal.cache_candles("nse_fo|123|OCT" if has_cache else "nse_fo|456|NOV", prior)
    asyncio.run(agent._prepare_history(object(), {"instrument_token": "123", "symbol": "OCT"}))
    assert len(agent.engine.candles) == (25 if has_cache else 0)
    assert "unavailable" in agent.state["warmup_history"]
    assert agent.engine.diagnostics()["phase"] == "warming_up"
    assert agent.engine.diagnostics()["required_candles"] == 3
