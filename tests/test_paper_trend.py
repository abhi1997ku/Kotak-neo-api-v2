import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.paper_trading import OneMinuteCandle
from backend.paper_trend import OpeningTrendEngine, TrendRunnerRules, STRATEGY_VERSION
from backend.paper_journal import PaperJournal
from backend import paper_agent

INDIA = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026,10,12,9,20,tzinfo=INDIA)
OPEN = int(NOW.replace(minute=15).timestamp()//60)


def engine_bars(step=10, count=3):
    return [OneMinuteCandle(OPEN+i,55000+step*i,55005+step*i,54995+step*i,55000+step*i,100) for i in range(count)]


@pytest.mark.parametrize("step,direction",[(10,"long"),(-10,"short")])
def test_early_readiness_without_yesterday_or_25_bars(step,direction):
    engine=OpeningTrendEngine()
    bars=engine_bars(step)
    engine.candles=bars[:2]
    assert engine._short_trigger() is None
    engine.candles=bars
    assert engine._short_trigger()==direction
    assert engine._short_trigger() is None
    assert engine.diagnostics()["required_candles"]==3


def test_mixed_open_and_missing_opening_history_do_not_signal():
    engine=OpeningTrendEngine()
    engine.candles=engine_bars(0)
    assert engine._short_trigger() is None
    engine.candles=engine_bars(10)
    for bar in engine.candles: bar.minute+=10
    assert engine._short_trigger() is None
    assert engine.diagnostics()["phase"]=="missing_opening_history"


def test_runner_has_no_fixed_target_and_stop_never_recedes():
    rules=TrendRunnerRules()
    trade=rules.open_trade("PE",90,100,"short")
    assert trade.target_price is None
    rules.on_tick(trade,130)
    assert trade.stop_price==105
    rules.on_tick(trade,200)
    assert trade.status=="open" and trade.stop_price==170
    rules.on_tick(trade,190)
    assert trade.stop_price==170
    rules.on_tick(trade,169)
    assert trade.exit_reason=="trailing_stop" and trade.exit_price==169


@pytest.mark.parametrize("step,direction", [(10,"long"),(-10,"short")])
def test_late_start_skips_partial_minute_and_requires_three_complete_bars(step,direction):
    engine=OpeningTrendEngine(allow_late_start=True)
    bars=engine_bars(step,4)
    for bar in bars: bar.minute+=2
    engine.candles=bars[:3]
    assert engine._short_trigger() is None
    assert engine.diagnostics()['phase']=='late_start_warming_up'
    engine.candles=bars
    assert engine._short_trigger()==direction
    assert engine._short_trigger() is None
    assert engine.diagnostics()['readiness_mode']=='observed_session'
    assert engine.diagnostics()['anchor_minute']==bars[1].minute


def test_late_start_gap_requires_fresh_confirmation_and_restart_avoids_duplicate():
    engine=OpeningTrendEngine(allow_late_start=True)
    bars=engine_bars(10,5)
    for bar in bars: bar.minute+=2
    bars[-1].minute+=5
    engine.candles=bars
    assert engine._short_trigger() is None
    assert engine.diagnostics()['analysis_candles']==1
    bars=engine_bars(10,4)
    for bar in bars: bar.minute+=2
    engine.load_completed_candles(bars,allow_initial_signal=False)
    assert engine._short_trigger() is None


def test_agent_enables_late_start_recovery(agent):
    assert agent.engine.allow_late_start is True


@pytest.fixture
def agent(tmp_path,monkeypatch):
    path=tmp_path/'journal.db'
    monkeypatch.setattr(paper_agent,"PaperJournal",lambda: PaperJournal(path))
    result=paper_agent.PaperAgent()
    monkeypatch.setattr(result,"_india_now",lambda:NOW)
    result._reset_day()
    result.state["running"]=True
    result.latest_future_received=time.monotonic()
    result.latest_future={"ltp":55020}
    return result


def test_loss_limit_cooldown_stale_feed_and_cutoff(agent,monkeypatch):
    assert agent._entry_block() is None
    agent.state["losses_today"]=2
    assert agent._entry_block()=="Daily loss lockout"
    agent.state["losses_today"]=0
    agent.state["realized_pnl_today"]=-5400
    assert agent._entry_block()=="Daily loss lockout"
    agent.state["realized_pnl_today"]=0
    agent.last_closed_at=NOW-timedelta(minutes=5)
    assert 'cooldown' in agent._entry_block()
    agent.last_closed_at=None
    agent.latest_future_received=time.monotonic()-10
    assert 'stale' in agent._entry_block()
    monkeypatch.setattr(agent,"_india_now",lambda:NOW.replace(hour=15,minute=1))
    assert 'Outside' in agent._entry_block()


def test_position_and_trailing_state_survive_restart(agent):
    agent.trade=agent.rules.open_trade("PE",90,100,"short")
    agent.trade_token="123"
    agent.rules.on_tick(agent.trade,170)
    agent.last_premium=170
    agent._save_position()
    restarted=paper_agent.PaperAgent()
    assert restarted.trade.stop_price==140
    assert restarted.trade.peak_premium==170
    assert restarted.trade_token=="123"


def test_slow_option_poll_does_not_block_futures_ingestion(agent,monkeypatch):
    async def exercise():
        quote_wait=asyncio.Event()
        progressed=asyncio.Event()
        seen=[]
        agent.trade=agent.rules.open_trade("PE",90,100,"short")
        agent.trade_token="123"
        async def slow_quote(client):
            await quote_wait.wait()
            return 100
        monkeypatch.setattr(agent,"_option_premium",slow_quote)
        async def prepare(client,tick):
            agent.history_contract="nse_fo|1|FUT"
        monkeypatch.setattr(agent,"_prepare_history",prepare)
        def record(*args):
            seen.append(args)
        monkeypatch.setattr(agent.engine,"on_tick",record)
        class Client:
            async def banknifty_future_tick_stream(self):
                for i in range(4):
                    await asyncio.sleep(0)
                    yield {"type":"quote","instrument_token":"1","symbol":"FUT","ltp":55000+i,"volume":100+i,"last_trade_time":NOW.timestamp(),"updated_at":NOW.isoformat()}
                progressed.set()
                await quote_wait.wait()
        worker=asyncio.create_task(agent._run(Client()))
        await asyncio.wait_for(progressed.wait(),1)
        assert len(seen)==4
        assert not quote_wait.is_set()
        worker.cancel()
        await asyncio.gather(worker,return_exceptions=True)
        assert agent.position_task is None
    asyncio.run(exercise())


def test_entry_rechecks_price_after_slow_lookup(agent,monkeypatch):
    agent.engine.candles=engine_bars()
    async def lookup(*args):
        agent.latest_future={"ltp":55060}
        return {"symbol":"CE","token":"1","lot_size":30,"premium":100}
    monkeypatch.setattr(agent,"_select_option",lookup)
    asyncio.run(agent._enter(object(),"long",55020))
    assert agent.trade is None
    assert 'moved more than' in agent.state['message']


def test_status_identifies_version_and_persistent_loss_count(agent):
    agent.journal.close_trade({"opened_at":"2026-10-12T03:50:00+00:00","closed_at":"2026-10-12T03:51:00+00:00","symbol":"PE","quantity":90,"entry_price":100,"exit_price":70,"points_pnl":-30,"unrealized_pnl":-2700,"exit_reason":"stop_loss"})
    agent.trade_day=""
    status=agent.status()
    assert status['strategy_version']==STRATEGY_VERSION
    assert status['losses_today']==1
    assert status['realized_pnl_today']==-2700


def test_restart_does_not_repeat_opening_signal_before_25_bars():
    engine=OpeningTrendEngine()
    engine.load_completed_candles(engine_bars(count=4),allow_initial_signal=False)
    engine.candles.append(engine_bars(count=5)[-1])
    assert engine._short_trigger() is None


def test_entry_and_close_journal_are_restart_consistent(agent,monkeypatch):
    agent.engine.candles=engine_bars()
    async def selected(*args):
        return {"symbol":"CE","token":"1","lot_size":30,"premium":100}
    monkeypatch.setattr(agent,"_select_option",selected)
    asyncio.run(agent._enter(object(),"long",55020))
    assert agent.trade is not None
    assert agent.journal.entries_for_day(agent.trade_day)==1
    assert agent.journal.load_position()['trade']['entry_price']==100
    agent.rules.on_tick(agent.trade,69)
    closed=agent._snapshot_trade(69)
    agent._close_trade(69,"test exit")
    agent.journal.close_trade(closed,clear_position=True)
    assert agent.journal.report()['trade_count']==1
    assert agent.journal.load_position() is None


def test_reject_entry_if_remaining_daily_budget_is_insufficient(agent,monkeypatch):
    agent.engine.candles=engine_bars()
    agent.state['realized_pnl_today']=-4000
    async def selected(*args):
        return {"symbol":"CE","token":"1","lot_size":30,"premium":100}
    monkeypatch.setattr(agent,"_select_option",selected)
    asyncio.run(agent._enter(object(),"long",55020))
    assert agent.trade is None
    assert 'risk budget' in agent.state['message']


def test_failed_entry_transaction_does_not_leave_phantom_position(agent,monkeypatch):
    agent.engine.candles=engine_bars()
    async def selected(*args):
        return {"symbol":"CE","token":"1","lot_size":30,"premium":100}
    monkeypatch.setattr(agent,"_select_option",selected)
    def fail(*args):
        raise RuntimeError('storage unavailable')
    monkeypatch.setattr(agent.journal,"open_position",fail)
    asyncio.run(agent._enter(object(),"long",55020))
    assert agent.trade is None
    assert agent.state['trades_today']==0


@pytest.mark.parametrize("loss,lot,allowed", [
    (2857.50,30,True), (2970,30,True), (2970.01,30,False),
    (0,33,True), (0,34,False), (5400,30,False),
    (-1000,34,False),
])
def test_risk_tolerance_boundaries(agent,monkeypatch,loss,lot,allowed):
    agent.engine.candles=engine_bars()
    agent.state['realized_pnl_today']=-loss
    async def selected(*args):
        return {"symbol":"CE","token":"1","lot_size":lot,"premium":100}
    monkeypatch.setattr(agent,"_select_option",selected)
    asyncio.run(agent._enter(object(),"long",55020))
    assert (agent.trade is not None) is allowed
    if allowed:
        import json
        event=next(e for e in agent.journal.day_audit(agent.trade_day)['events'] if e['event_type']=='trade_opened')
        details=json.loads(event['details'])
        assert details['planned_risk']==lot*3*30
        assert details['risk_tolerance']==270


def test_risk_tolerance_does_not_override_two_loss_lockout(agent,monkeypatch):
    agent.state['losses_today']=2
    agent.state['realized_pnl_today']=-100
    assert agent._entry_block()=='Daily loss lockout'
